import type { App } from '../app';
import { downloadText } from '../export/download';
import {
  effectiveHeight,
  formatEffective,
  HeightDrafts,
  type LayerHeightInfo,
  type LayersConfig,
  type ProjectClient,
} from '../project/heights';
import { ProjectLoadError, type ProjectChanges } from '../project/review';
import { LayerManager } from '../scene/layers';
import { button, compactNumber, formatNumber, h, icon } from './dom';
import { ProjectReviewSection } from './projectReview';

const KIND_LABEL: Record<string, string> = {
  building: 'Edificio',
  road: 'Vial',
  water: 'Agua',
  vegetation: 'Vegetación',
  terrain: 'Terreno',
  line: 'Líneas',
};

const SOURCE_LABEL: Record<string, string> = {
  viewer: 'fijada aquí',
  fixed: 'fija en el YAML',
  text: 'de rótulos',
  attribute: 'de atributos',
  elevation: 'de la cota Z',
  block: 'por bloque',
  floors: 'plantas × altura de planta',
  default: 'provisional',
  none: 'sin altura',
};

const NOT_CONVERTED =
  'Este modelo no procede de un plano convertido en el servidor (es un GLB o un ejemplo): no ' +
  'hay DWG/DXF ni configuración con que repetir la conversión, así que su altura no se puede ' +
  'cambiar aquí. Convierte el plano desde la pantalla de inicio.';

/** Tiempo para confirmar con un segundo clic una acción que descarta trabajo. */
const CONFIRM_MS = 5000;

export type HeightProgress = (label: string, fraction: number | null) => void;

/** Lo que el panel necesita del proyecto abierto para aplicar alturas. */
export interface HeightsHost {
  client: ProjectClient;
  /**
   * Reconvierte con esos cambios (alturas, apoyo, tipo de capa) y sustituye el modelo solo si
   * termina bien. Lanza ProjectLoadError si se generó pero el visor no pudo cargarlo.
   */
  apply(changes: ProjectChanges, onProgress: HeightProgress): Promise<void>;
  /** Recupera el resultado anterior a la última reconversión y lo carga. */
  restore(onProgress: HeightProgress): Promise<void>;
  /** Vuelve a cargar un resultado ya generado que no llegó a cargarse. */
  retryLoad(): Promise<void>;
  /** Revisión confirmada (materiales, ediciones) que se perdería al reconvertir, o null. */
  confirmedRevision(): string | null;
  /** Nombre del proyecto para el archivo YAML descargado. */
  projectName(): string;
}

/**
 * Pestaña Capas: visibilidad, color, aislar, selección (resalta la capa en el modelo) y altura
 * por capa (reconvierte el plano con el YAML actualizado).
 */
export class LayersPanel {
  readonly element = h('div', { class: 'tab-panel' });
  private readonly list = h('ul', { class: 'layer-list' });
  private readonly search = h('input', {
    type: 'search',
    class: 'input search',
    placeholder: 'Filtrar capas',
    spellcheck: false,
  });
  private readonly summary = h('span', { class: 'mono' });
  private readonly selection = h('div', { class: 'layer-selection', hidden: true });
  private readonly heights = h('div', { class: 'layer-heights' });
  private readonly drafts = new HeightDrafts();
  private job: string | null = null;
  private config: LayersConfig | null = null;
  private configError: string | null = null;
  private loadToken = 0;
  private applying: { label: string; fraction: number | null } | null = null;
  private applyError: string | null = null;
  /** La última reconversión terminó en el servidor, pero su modelo no se pudo cargar. */
  private notLoaded: string | null = null;
  private armed = false;
  private readonly review: ProjectReviewSection;
  private reviewToken = 0;
  private renderPending = false;

  constructor(
    private readonly app: App,
    private readonly host: HeightsHost,
  ) {
    this.search.addEventListener('input', () => this.render());
    this.review = new ProjectReviewSection({
      apply: (changes) =>
        void this.runChanges(changes, 'Proyecto reconvertido con los cambios de la revisión.'),
      restore: () => void this.restore(),
      busy: () => this.applying !== null,
    });
    this.review.set(null);
    this.element.append(
      h(
        'div',
        { class: 'panel-toolbar' },
        this.search,
        button({
          icon: 'eye',
          title: 'Mostrar todas',
          variant: 'ghost',
          size: 'sm',
          onClick: () => {
            LayerManager.showAll(app.state.layers);
            this.apply();
          },
        }),
      ),
      h(
        'div',
        { class: 'list-header' },
        h('span', { textContent: 'Capa (púlsala para resaltarla)' }),
        h('span', { textContent: 'Tris' }),
      ),
      this.list,
      this.selection,
      this.review.element,
      this.heights,
      h('div', { class: 'panel-footer' }, this.summary),
    );
    app.on('model', () => this.render());
    app.on('state', () => this.render());
    // Al salir de un campo se pinta lo que quedó pendiente mientras se escribía
    this.list.addEventListener('focusout', () => {
      if (this.renderPending) queueMicrotask(() => this.render());
    });
  }

  get count(): number {
    return this.app.layers.layers.size;
  }

  /**
   * Proyecto del modelo cargado: id del trabajo del servidor, o null si el modelo no procede de
   * una conversión (GLB abierto, ejemplo). Recupera las alturas guardadas y las pendientes.
   */
  setProject(job: string | null): void {
    this.job = job;
    this.config = null;
    this.configError = null;
    this.applyError = null;
    this.notLoaded = null;
    this.armed = false;
    this.drafts.open(job);
    this.review.set(null, null, job !== null);
    this.render();
    if (job) {
      void this.loadConfig();
      void this.loadReview();
    }
  }

  /** Apoyo por capa, capas pendientes y estado del servidor para el proyecto abierto. */
  private async loadReview(): Promise<void> {
    const job = this.job;
    if (!job) return;
    const token = ++this.reviewToken;
    try {
      const review = await this.host.client.review(job);
      if (token === this.reviewToken && job === this.job) this.review.set(review);
    } catch (error) {
      if (token !== this.reviewToken || job !== this.job) return;
      // Un modelo que no viene de un plano no tiene nada que revisar: se explica más abajo
      const kind = (error as { kind?: string }).kind;
      const message = error instanceof Error ? error.message : String(error);
      this.review.set(null, kind === 'not_repeatable' ? null : message);
    }
  }

  private async loadConfig(): Promise<void> {
    const job = this.job;
    if (!job) return;
    const token = ++this.loadToken;
    try {
      const config = await this.host.client.layersConfig(job);
      if (token !== this.loadToken || job !== this.job) return;
      this.config = config;
      this.configError = null;
    } catch (error) {
      if (token !== this.loadToken || job !== this.job) return;
      this.config = null;
      this.configError = error instanceof Error ? error.message : String(error);
    }
    this.render(true);
  }

  private apply(): void {
    this.app.applyLayers();
    this.render();
  }

  private info(name: string): LayerHeightInfo | undefined {
    return this.config?.layers.find((layer) => layer.name === name);
  }

  private get canEdit(): boolean {
    return Boolean(this.job && this.config?.repeatable && !this.applying);
  }

  /** Capas con cambios pendientes válidos o no (para el contador y el botón). */
  private pendingLayers(): string[] {
    const editable = new Set(
      (this.config?.layers ?? []).filter((l) => l.editable).map((l) => l.name),
    );
    return this.drafts.pending.filter((layer) => editable.has(layer));
  }

  render(force = false): void {
    // Con un campo de altura enfocado no se reconstruye la lista: se perdería lo que se escribe
    if (!force && this.list.contains(document.activeElement)) {
      const active = document.activeElement as HTMLElement | null;
      if (active?.classList.contains('layer-height-input')) {
        this.renderPending = true;
        this.renderHeights();
        return;
      }
    }
    this.renderPending = false;
    const { app } = this;
    const query = this.search.value.trim().toLowerCase();
    const infos = [...app.layers.layers.values()];
    const shown = infos.filter((info) => info.name.toLowerCase().includes(query));
    this.list.replaceChildren(
      ...shown.map((info) => {
        const state = app.state.layers[info.name] ?? { visible: true };
        const selected = app.selectedLayer === info.name;
        const eye = h(
          'button',
          {
            type: 'button',
            class: 'eye',
            title: state.visible ? 'Ocultar' : 'Mostrar',
            attrs: { 'aria-pressed': String(state.visible) },
          },
          icon(state.visible ? 'eye' : 'eye-off', 14),
        );
        eye.addEventListener('click', () => {
          state.visible = !state.visible;
          this.apply();
        });
        const original = `#${info.originalColors[0]?.getHexString() ?? 'cccccc'}`;
        const color = h('input', {
          type: 'color',
          class: 'swatch',
          value: state.colorOverride ?? original,
          title: state.colorOverride ? 'Color modificado (doble clic: original)' : 'Color',
        });
        color.addEventListener('input', () => {
          state.colorOverride = color.value;
          app.applyLayers();
        });
        color.addEventListener('dblclick', (e) => {
          e.preventDefault();
          delete state.colorOverride;
          this.apply();
        });
        const isolate = button({
          icon: 'isolate',
          title: 'Aislar (ocultar las demás)',
          variant: 'ghost',
          size: 'sm',
          onClick: () => {
            LayerManager.isolate(app.state.layers, info.name);
            this.apply();
          },
        });
        const kind = KIND_LABEL[info.kind] ?? info.kind;
        const name = h('button', {
          type: 'button',
          class: 'layer-name',
          textContent: info.name,
          title: selected
            ? 'Quitar la selección'
            : `Resaltar en el modelo todos los elementos de ${info.name}`,
          attrs: { 'aria-pressed': String(selected) },
        });
        name.addEventListener('click', () => app.selectLayer(selected ? null : info.name));
        const row = h(
          'li',
          {
            class: `layer-row${state.visible ? '' : ' is-off'}${selected ? ' is-selected' : ''}`,
            title: `${info.name} · ${kind} · ${formatNumber(info.triangles)} triángulos`,
          },
          eye,
          color,
          name,
          h('span', { class: 'layer-tris mono', textContent: compactNumber(info.triangles) }),
          isolate,
        );
        const height = this.heightRow(info.name, formatEffective(effectiveHeight(info.nodes)));
        if (height) row.append(height);
        return row;
      }),
    );
    if (shown.length === 0) {
      this.list.append(
        h('li', {
          class: 'empty',
          textContent: infos.length ? 'Sin coincidencias' : 'Sin capas',
        }),
      );
    }
    const visible = infos.filter((i) => app.state.layers[i.name]?.visible !== false).length;
    const tris = infos.reduce((sum, i) => sum + i.triangles, 0);
    this.summary.textContent = `${visible}/${infos.length} visibles · ${compactNumber(tris)} tris`;
    this.renderSelection();
    this.renderHeights();
  }

  /** Segunda línea de una capa: altura efectiva, campo para cambiarla y su estado. */
  private heightRow(layer: string, effective: string): HTMLElement | null {
    const info = this.info(layer);
    if (effective === '—' && !info?.editable) return null; // capa sin altura (terreno, líneas)
    const line = h('div', { class: 'layer-height' });
    const current = h('span', {
      class: 'layer-height-current mono',
      textContent: effective,
      title: 'Altura efectiva usada en la conversión del modelo cargado',
    });
    line.append(h('span', { class: 'layer-height-label', textContent: 'Altura' }), current);
    if (!info?.editable || !this.config?.repeatable) {
      if (info && !info.editable && info.reason) line.title = info.reason;
      return line;
    }
    const draft = this.drafts.get(layer);
    const saved = info.fixed;
    const pending = draft !== undefined;
    const error = this.drafts.error(layer);
    const input = h('input', {
      type: 'text',
      inputMode: 'decimal',
      class: `input num layer-height-input${error ? ' is-invalid' : ''}`,
      value: draft ?? (saved !== null ? String(saved) : ''),
      placeholder: draft === null ? 'original' : 'nueva',
      disabled: !this.canEdit,
      spellcheck: false,
      attrs: { 'aria-label': `Nueva altura de ${layer} en metros`, autocomplete: 'off' },
    });
    const commit = (): void => {
      this.drafts.set(layer, input.value, saved);
      this.armed = false;
    };
    input.addEventListener('input', () => {
      commit();
      const problem = this.drafts.error(layer);
      input.classList.toggle('is-invalid', problem !== null);
      this.renderHeights();
    });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') input.blur();
      if (e.key === 'Escape') {
        this.drafts.revert(layer);
        input.blur();
        this.render(true);
      }
    });
    input.addEventListener('change', () => this.render(true));
    line.append(input, h('span', { class: 'unit', textContent: 'm' }));

    const status = h('span', { class: 'layer-height-status' });
    if (pending) {
      status.classList.add('is-pending');
      status.textContent = draft === null ? 'pendiente: quitar' : 'pendiente';
      status.title =
        draft === null
          ? 'Al aplicar, la capa vuelve a la altura de su regla original'
          : `Aún sin aplicar${saved !== null ? ` · guardada: ${saved} m` : ''}`;
    } else if (saved !== null) {
      status.classList.add('is-saved');
      status.textContent = 'guardada';
      status.title = `Altura fijada en el YAML del proyecto: ${saved} m`;
    } else {
      status.textContent = SOURCE_LABEL[info.source ?? ''] ?? '';
      status.title = 'De dónde sale hoy la altura de esta capa';
    }
    line.append(status);
    if (pending) {
      line.append(
        button({
          icon: 'refresh',
          title:
            saved !== null
              ? `Recuperar el valor guardado (${saved} m)`
              : 'Descartar el cambio pendiente',
          variant: 'ghost',
          size: 'sm',
          onClick: () => {
            this.drafts.revert(layer);
            this.render(true);
          },
        }),
      );
    } else if (saved !== null && this.canEdit) {
      line.append(
        button({
          icon: 'close',
          title: 'Quitar la altura fijada: la capa vuelve a su regla original al aplicar',
          variant: 'ghost',
          size: 'sm',
          onClick: () => {
            this.drafts.remove(layer, saved);
            this.render(true);
          },
        }),
      );
    }
    if (error) {
      line.append(h('span', { class: 'layer-height-error', textContent: error }));
      line.classList.add('has-error');
    }
    return line;
  }

  private renderSelection(): void {
    const name = this.app.selectedLayer;
    this.selection.hidden = name === null;
    if (name === null) return;
    const hidden = this.app.state.layers[name]?.visible === false;
    this.selection.replaceChildren(
      h('span', {
        class: 'layer-selection-text',
        textContent: hidden
          ? `«${name}» seleccionada, pero está oculta: muéstrala para verla resaltada.`
          : `«${name}» resaltada en el modelo.`,
      }),
      button({
        label: 'Quitar selección',
        variant: 'ghost',
        size: 'sm',
        onClick: () => this.app.selectLayer(null),
      }),
    );
  }

  /** Pie de alturas: estado del proyecto, acciones, progreso y errores. */
  private renderHeights(): void {
    const box = this.heights;
    box.replaceChildren();
    if (!this.app.hasModel) return;
    const note = (text: string, kind = ''): HTMLElement =>
      h('p', { class: `layer-heights-note${kind ? ` ${kind}` : ''}`, textContent: text });
    box.append(h('div', { class: 'group-title', textContent: 'Altura por capa' }));
    if (!this.job) {
      box.append(note(NOT_CONVERTED));
      return;
    }
    if (this.configError) {
      box.append(
        note(`No se pudo leer la configuración del proyecto: ${this.configError}`, 'is-error'),
        button({
          label: 'Reintentar',
          variant: 'secondary',
          size: 'sm',
          onClick: () => void this.loadConfig(),
        }),
      );
      return;
    }
    if (!this.config) {
      box.append(note('Leyendo la configuración del proyecto…'));
      return;
    }
    if (!this.config.repeatable) {
      box.append(note(this.config.reason ?? NOT_CONVERTED));
      return;
    }
    if (this.applying) {
      const { label, fraction } = this.applying;
      const bar = h('div', { class: 'progress-bar' });
      if (fraction !== null) bar.style.width = `${(fraction * 100).toFixed(1)}%`;
      box.append(
        h(
          'div',
          {
            class: `layer-heights-progress${fraction === null ? ' is-indeterminate' : ''}`,
            attrs: { role: 'status', 'aria-live': 'polite' },
          },
          h('span', {
            textContent: `${label}${fraction === null ? '…' : ` · ${Math.round(fraction * 100)} %`}`,
          }),
          h('div', { class: 'progress-track' }, bar),
        ),
        note('El modelo actual sigue en pantalla hasta que la conversión termine bien.'),
      );
      return;
    }
    if (this.notLoaded) {
      box.append(
        note(`Generado pero no cargado: ${this.notLoaded}`, 'is-error'),
        note(
          'La conversión terminó y el proyecto del servidor ya está actualizado, pero el visor sigue mostrando el modelo anterior.',
        ),
        button({
          label: 'Reintentar carga',
          icon: 'refresh',
          variant: 'primary',
          size: 'sm',
          onClick: () => void this.retryLoad(),
        }),
      );
      return;
    }
    const pending = this.pendingLayers();
    const invalid = pending.filter((layer) => this.drafts.error(layer) !== null);
    const fixed = Object.keys(this.config.heights).length;
    box.append(
      note(
        pending.length
          ? `${pending.length} ${pending.length === 1 ? 'capa con cambios' : 'capas con cambios'} sin aplicar.`
          : fixed
            ? `${fixed} ${fixed === 1 ? 'altura guardada' : 'alturas guardadas'} en la configuración del proyecto.`
            : 'Escribe una altura en metros en una capa: todos sus elementos pasarán a esa altura.',
      ),
    );
    if (this.applyError) box.append(note(this.applyError, 'is-error'));
    const revision = this.host.confirmedRevision();
    if (pending.length && revision) {
      box.append(
        note(
          `Aplicar reconvierte el plano: el modelo nuevo no tendrá los materiales y ediciones confirmados (${revision}). El resultado anterior queda guardado y se puede recuperar desde «Revisión del proyecto».`,
          'is-warning',
        ),
      );
    }
    const apply = button({
      label: this.armed ? 'Confirmar y reconvertir' : 'Aplicar alturas',
      icon: 'check',
      variant: 'primary',
      size: 'sm',
      onClick: () => void this.applyHeights(),
    });
    apply.disabled = pending.length === 0 || invalid.length > 0;
    if (invalid.length) apply.title = 'Corrige las alturas marcadas en rojo';
    const discard = button({
      label: 'Descartar cambios',
      variant: 'ghost',
      size: 'sm',
      onClick: () => {
        this.drafts.clear();
        this.armed = false;
        this.render(true);
      },
    });
    discard.hidden = pending.length === 0;
    box.append(
      h(
        'div',
        { class: 'mat-row-actions' },
        apply,
        discard,
        button({
          label: 'Descargar YAML',
          icon: 'download',
          title: 'Configuración asociada al proyecto (la usada en la última conversión)',
          variant: 'ghost',
          size: 'sm',
          onClick: () => void this.downloadConfig(),
        }),
      ),
    );
  }

  private async downloadConfig(): Promise<void> {
    if (!this.job) return;
    try {
      const text = await this.host.client.configText(this.job);
      downloadText(text, `${this.host.projectName()}.yaml`, 'application/yaml');
    } catch (error) {
      this.app.notify(error instanceof Error ? error.message : String(error), 'error');
    }
  }

  private async applyHeights(): Promise<void> {
    if (!this.job || !this.config || this.applying) return;
    const editable = new Set(this.config.layers.filter((l) => l.editable).map((l) => l.name));
    let changes: Record<string, number | null>;
    try {
      changes = this.drafts.changes(editable);
    } catch (error) {
      this.applyError = error instanceof Error ? error.message : String(error);
      this.render(true);
      return;
    }
    if (Object.keys(changes).length === 0) return;
    // Con materiales o ediciones confirmados, un segundo clic confirma que se perderán
    if (this.host.confirmedRevision() && !this.armed) {
      this.armed = true;
      this.render(true);
      setTimeout(() => {
        if (!this.armed) return;
        this.armed = false;
        this.render();
      }, CONFIRM_MS);
      return;
    }
    this.armed = false;
    await this.runChanges({ heights: changes }, 'Alturas aplicadas: modelo reconvertido.', true);
  }

  /** Envía cambios del proyecto, sigue la reconversión y deja el panel al día. */
  private async runChanges(
    changes: ProjectChanges,
    done: string,
    clearDrafts = false,
  ): Promise<void> {
    if (!this.job || this.applying) return;
    await this.track('Enviando cambios', done, clearDrafts, (progress) =>
      this.host.apply(changes, progress),
    );
  }

  private async restore(): Promise<void> {
    if (!this.job || this.applying) return;
    await this.track(
      'Recuperando el resultado anterior',
      'Resultado anterior recuperado.',
      false,
      (p) => this.host.restore(p),
    );
  }

  private async retryLoad(): Promise<void> {
    if (!this.job || this.applying) return;
    await this.track('Cargando el modelo generado', 'Modelo cargado.', false, () =>
      this.host.retryLoad(),
    );
  }

  private async track(
    label: string,
    done: string,
    clearDrafts: boolean,
    run: (progress: HeightProgress) => Promise<void>,
  ): Promise<void> {
    const job = this.job;
    this.applyError = null;
    this.notLoaded = null;
    this.applying = { label, fraction: null };
    this.render(true);
    this.review.render();
    try {
      await run((text, fraction) => {
        this.applying = { label: text, fraction };
        this.renderHeights();
      });
      if (clearDrafts && job === this.job) this.drafts.clear();
      this.app.notify(done, 'success');
    } catch (error) {
      const fix = (error as { fix?: string }).fix;
      const message = `${error instanceof Error ? error.message : String(error)}${fix ? ` ${fix}` : ''}`;
      if (error instanceof ProjectLoadError) {
        // El servidor ya tiene el resultado nuevo: lo pendiente es solo cargarlo
        this.notLoaded = message;
        if (clearDrafts && job === this.job) this.drafts.clear();
      } else {
        this.applyError = `${message} El modelo anterior sigue cargado.`;
      }
    } finally {
      this.applying = null;
      if (job === this.job) await Promise.all([this.loadConfig(), this.loadReview()]);
      this.render(true);
    }
  }
}
