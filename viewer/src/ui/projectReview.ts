/**
 * Revisión del proyecto en la pestaña Capas: qué capas no se apoyan bien en el terreno, cuáles
 * se quedaron sin convertir y tienen geometría, y qué valores son provisionales. Permite
 * resolverlo sin editar el YAML: apoyo automático por capa y tipo de las capas pendientes.
 *
 * Solo recoge la decisión del usuario; aplicar (reconvertir y recargar) lo hace el panel.
 */
import {
  KIND_OPTIONS,
  type LayerAdjust,
  type LayerKind,
  type ProjectChanges,
  type ProjectReview,
  reviewIssues,
  serverNotice,
  type SupportLayer,
} from '../project/review';
import { button, h } from './dom';

const MODE_LABEL: Record<string, string> = {
  auto: 'apoyo automático',
  terrain: 'sobre el terreno',
  elevation: 'cota Z de cada huella',
  fixed: 'cota fija',
};

export interface ReviewHandlers {
  /** Reconvierte con esos cambios (el panel muestra el progreso y recarga el modelo). */
  apply(changes: ProjectChanges): void;
  /** Recupera el resultado anterior a la última reconversión. */
  restore(): void;
  busy(): boolean;
}

export class ProjectReviewSection {
  readonly element = h('div', { class: 'layer-heights project-review' });
  private review: ProjectReview | null = null;
  private error: string | null = null;
  private loading = false;
  /** Capas marcadas para pasar al apoyo automático. */
  private readonly repair = new Set<string>();
  /** Tipo elegido para cada capa pendiente ('' = sin decidir). */
  private readonly kinds = new Map<string, LayerKind | ''>();

  constructor(private readonly handlers: ReviewHandlers) {}

  /** Estado de la revisión: null si no hay proyecto reconvertible que revisar. */
  set(review: ProjectReview | null, error: string | null = null, loading = false): void {
    const job = this.review?.job;
    this.review = review;
    this.error = error;
    this.loading = loading;
    if (review && review.job !== job) {
      this.repair.clear();
      this.kinds.clear();
    }
    if (review) {
      // Por defecto, lo que Extrude propone; el usuario puede desmarcarlo
      for (const layer of review.support) {
        if (!layer.canRepair) this.repair.delete(layer.layer);
        else if (layer.recommended && review.job !== job) this.repair.add(layer.layer);
      }
      const pending = new Set((review.pending ?? []).map((p) => p.name));
      for (const name of [...this.kinds.keys()]) if (!pending.has(name)) this.kinds.delete(name);
    }
    this.render();
  }

  /** Cambios elegidos, listos para enviar; null si no hay ninguno. */
  changes(): ProjectChanges | null {
    const adjust: Record<string, LayerAdjust> = {};
    for (const layer of this.repair) adjust[layer] = { support: 'auto' };
    for (const [layer, kind] of this.kinds) {
      if (kind) adjust[layer] = { ...adjust[layer], kind };
    }
    return Object.keys(adjust).length ? { adjust } : null;
  }

  private note(text: string, kind = ''): HTMLElement {
    return h('p', { class: `layer-heights-note${kind ? ` ${kind}` : ''}`, textContent: text });
  }

  render(): void {
    const box = this.element;
    box.replaceChildren();
    const review = this.review;
    if (!review && !this.error && !this.loading) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    box.append(h('div', { class: 'group-title', textContent: 'Revisión del proyecto' }));
    if (this.error) {
      box.append(this.note(`No se pudo revisar el proyecto: ${this.error}`, 'is-error'));
      return;
    }
    if (!review) {
      box.append(this.note('Revisando apoyo y capas…'));
      return;
    }
    const notice = serverNotice(review.build);
    if (notice) box.append(this.note(notice, 'is-warning'));

    const issues = reviewIssues(review);
    if (issues.length === 0) {
      box.append(
        this.note(
          'Sin incidencias: lo convertido se apoya en el terreno y no hay capas pendientes.',
        ),
      );
    } else {
      const list = h('ul', { class: 'review-issues' });
      for (const issue of issues) {
        list.append(h('li', { class: `is-${issue.kind}`, textContent: issue.text }));
      }
      box.append(list);
    }
    if (review.terrain.status === 'valid' && review.terrain.z_min_m !== undefined) {
      box.append(
        this.note(
          `Terreno de apoyo: ${(review.terrain.layers ?? []).join(', ') || 'capas de terreno'}, de ${review.terrain.z_min_m.toFixed(1)} a ${(review.terrain.z_max_m ?? 0).toFixed(1)} m.`,
        ),
      );
    }

    const busy = this.handlers.busy() || !review.repeatable;
    this.renderSupport(review, busy);
    this.renderPending(review, busy);

    const changes = this.changes();
    const count = changes ? Object.keys(changes.adjust ?? {}).length : 0;
    const apply = button({
      label: count ? `Aplicar y reconvertir (${count})` : 'Aplicar y reconvertir',
      icon: 'check',
      variant: 'primary',
      size: 'sm',
      onClick: () => {
        const current = this.changes();
        if (current) this.handlers.apply(current);
      },
    });
    apply.disabled = busy || count === 0;
    const actions = h('div', { class: 'mat-row-actions' }, apply);
    if (review.canRestore) {
      const undo = button({
        label: 'Recuperar el resultado anterior',
        icon: 'refresh',
        title:
          'Vuelve al modelo, la configuración, los materiales y las ediciones de antes de la última reconversión',
        variant: 'ghost',
        size: 'sm',
        onClick: () => this.handlers.restore(),
      });
      undo.disabled = this.handlers.busy();
      actions.append(undo);
    }
    box.append(actions);
    if (!review.repeatable) {
      box.append(
        this.note('El servidor ya no conserva el plano original: no se puede reconvertir.'),
      );
    }
    if (review.revision) {
      box.append(
        h('p', {
          class: 'layer-heights-note mono review-revision',
          textContent: `Revisión ${review.revision.replace(/^sha256:/, '').slice(0, 12)} · servidor ${review.build.id}`,
          title: `Geometría: ${review.revision}\nServidor arrancado: ${review.build.started}`,
        }),
      );
    }
  }

  private supportLine(layer: SupportLayer): string {
    const parts = [
      layer.floating ? `${layer.floating} flotan` : '',
      layer.partialGap ? `${layer.partialGap} contacto parcial` : '',
      layer.buried ? `${layer.buried} enterrados` : '',
      layer.partiallyBuried ? `${layer.partiallyBuried} parcialmente enterrados` : '',
      layer.intersecting ? `${layer.intersecting} cortan la ladera` : '',
      layer.extrapolated ? `${layer.extrapolated} fuera del terreno` : '',
    ].filter(Boolean);
    return `${MODE_LABEL[layer.baseMode] ?? layer.baseMode} · ${
      parts.length ? parts.join(', ') : 'sin incidencias'
    } (de ${layer.checked || layer.objects})`;
  }

  private renderSupport(review: ProjectReview, busy: boolean): void {
    const repairable = review.support.filter(
      (layer) =>
        layer.canRepair || layer.floating || layer.buried || layer.partialGap || layer.extrapolated,
    );
    if (repairable.length === 0) return;
    const list = h('ul', { class: 'review-list' });
    for (const layer of repairable) {
      const check = h('input', { type: 'checkbox', checked: this.repair.has(layer.layer) });
      check.disabled = busy || !layer.canRepair;
      check.addEventListener('change', () => {
        if (check.checked) this.repair.add(layer.layer);
        else this.repair.delete(layer.layer);
        this.render();
      });
      list.append(
        h(
          'li',
          {},
          h(
            'label',
            { class: 'review-item', title: layer.note },
            check,
            h(
              'span',
              { class: 'review-item-text' },
              h('strong', { textContent: layer.layer }),
              h('span', { class: 'review-item-meta', textContent: this.supportLine(layer) }),
              layer.canRepair
                ? h('span', {
                    class: 'review-item-meta',
                    textContent: layer.recommended
                      ? 'Propuesta: apoyar cada objeto en el terreno bajo su huella.'
                      : 'Base elegida de forma explícita: márcala solo si quieres apoyarla en el terreno.',
                  })
                : null,
            ),
          ),
        ),
      );
    }
    this.element.append(
      h('div', { class: 'review-subtitle', textContent: 'Apoyo en el terreno' }),
      list,
      this.note(
        'El apoyo automático coloca cada objeto sobre su propio terreno, con planta horizontal y zócalo si hay pendiente. No cambia las alturas ni mueve toda la capa a la vez.',
      ),
    );
  }

  private renderPending(review: ProjectReview, busy: boolean): void {
    if (!review.pending?.length) return;
    const list = h('ul', { class: 'review-list' });
    for (const pending of review.pending) {
      const select = h('select', {
        class: 'select',
        attrs: { 'aria-label': `Tipo de la capa ${pending.name}` },
      });
      select.append(h('option', { value: '', textContent: 'Decidir después' }));
      for (const option of KIND_OPTIONS) {
        select.append(
          h('option', {
            value: option.value,
            textContent:
              option.value === pending.suggested_kind
                ? `${option.label} (por el nombre)`
                : option.label,
          }),
        );
      }
      select.value = this.kinds.get(pending.name) ?? '';
      select.disabled = busy;
      select.addEventListener('change', () => {
        this.kinds.set(pending.name, select.value as LayerKind | '');
        this.render();
      });
      list.append(
        h(
          'li',
          { class: 'review-item review-pending' },
          h(
            'span',
            { class: 'review-item-text' },
            h('strong', { textContent: pending.name }),
            h('span', { class: 'review-item-meta', textContent: pending.summary }),
          ),
          select,
        ),
      );
    }
    this.element.append(
      h('div', { class: 'review-subtitle', textContent: 'Capas sin convertir con geometría' }),
      list,
      this.note(
        'No están en el modelo ni en los recuentos de apoyo. Si son edificios se crean con una altura provisional de 9 m, que después puedes fijar por capa.',
      ),
    );
  }
}
