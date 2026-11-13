/**
 * «Editar proyecto con IA» (Fase 3): petición libre → una consulta → plan de operaciones con
 * herramientas reales (materiales, altura, mover, girar, duplicar, vistas y sol).
 *
 * El usuario revisa la interpretación, marca operaciones (las dependencias se marcan solas),
 * resuelve dudas eligiendo objetos o dando datos, previsualiza y confirma; deshacer y rehacer
 * recorren las revisiones del proyecto. Solo «Proponer cambios» / «Actualizar propuesta»
 * consultan a la IA; todo lo demás es local o del servidor, sin IA.
 */
import type { App } from '../app';
import { ConversionError } from '../convert/client';
import {
  AiClient,
  AiRequestError,
  applicable,
  neededInputs,
  consentStore,
  defaultSelection,
  planIsCurrent,
  requestKey,
  RequestGate,
  toggleOperation,
  type AiProviderInfo,
  type AiProviders,
  type AiScope,
  type ApplyBody,
  type ApplyResult,
  type ContextPreview,
  type EditPlan,
  type OperationResult,
  type OperationInputs,
  type PlanOperation,
  type ProjectStatus,
  type ProposalRequest,
} from '../materials/ai';
import { SURFACE_NAMES, type CatalogObject, type SurfaceKey } from '../materials/catalog';
import type { Library } from '../materials/client';
import type { MaterialPlan } from '../materials/plan';
import {
  commitPrepared,
  prepareViewerOps,
  previewPrepared,
  redoEffects,
  restore,
  undoEffects,
  type ViewerEffects,
  type ViewsTarget,
} from '../materials/viewerOps';
import type { ViewerState } from '../state';
import { button, h, section } from './dom';

export interface EditHost {
  readonly app: App;
  readonly views: ViewsTarget;
  catalog(): Map<string, CatalogObject>;
  selected(): ReadonlySet<string>;
  mark(ids: string[]): void;
  usable(): boolean;
  /** Hay asignaciones manuales sin confirmar (se confirman o descartan antes). */
  manualDirty(): boolean;
  library(): Library | null;
  ensureJob(): Promise<string>;
  showPreview(url: string): Promise<void>;
  discardPreview(): Promise<void>;
  /** Tras confirmar, deshacer o rehacer: recarga el modelo del proyecto y su plan. */
  applyProject(status: ProjectStatus, plan: MaterialPlan | null | undefined): Promise<void>;
}

const SUMMARY = 4;
const STATUS_TEXT: Record<string, string> = {
  ready: 'Lista',
  needs_resolution: 'Elige objetos',
  needs_input: 'Falta un dato',
  blocked: 'Bloqueada',
  incompatible: 'No compatible',
};
const RESULT_TEXT: Record<OperationResult['status'], string> = {
  done: 'Hecho',
  viewer: 'Hecho en el visor',
  failed: 'Falló',
  not_applied: 'No aplicado',
  pending: 'Pendiente',
};
const PRESETS: Record<string, string> = {
  iso: 'isométrica',
  top: 'planta',
  north: 'desde el norte',
  south: 'desde el sur',
  east: 'desde el este',
  west: 'desde el oeste',
};
const TIMES: Record<string, string> = {
  sunset: 'atardecer',
  sunrise: 'amanecer',
  noon: 'mediodía',
};

function present(...nodes: (HTMLElement | null)[]): HTMLElement[] {
  return nodes.filter((n): n is HTMLElement => n !== null);
}

function list(title: string, items: string[], className = ''): HTMLElement | null {
  if (!items.length) return null;
  return h(
    'div',
    { class: `ai-block ${className}` },
    h('h4', { textContent: title }),
    h('ul', {}, ...items.map((text) => h('li', { textContent: text }))),
  );
}

function num(value: unknown): string {
  return typeof value === 'number' ? value.toLocaleString('es', { maximumFractionDigits: 2 }) : '—';
}

function signed(value: unknown): string {
  return `${Number(value) >= 0 ? '+' : ''}${num(value)}`;
}

const ASSETS: Record<string, string> = {
  tree_round: 'árbol de copa',
  tree_columnar: 'árbol columnar',
  shrub: 'arbusto',
  bench: 'banco',
  street_lamp: 'farola',
  litter_bin: 'papelera',
};
const ORIENTATIONS: Record<string, string> = {
  north: 'norte',
  south: 'sur',
  east: 'este',
  west: 'oeste',
  all: 'todas',
};

/** Lo que hará la operación, en palabras (solo a partir de sus argumentos). */
export function describeOperation(op: PlanOperation, materialName: (id: string) => string): string {
  const a = op.args;
  switch (op.tool) {
    case 'material.assign': {
      const surface = SURFACE_NAMES[a['surface'] as SurfaceKey] ?? String(a['surface']);
      return `${materialName(String(a['materialId']))} en ${surface}`;
    }
    case 'building.set_height': {
      const mode = String(a['mode']);
      if (mode === 'absolute_m') return `Altura total ${num(a['value'])} m`;
      if (mode === 'delta_m') return `Altura ${signed(a['value'])} m`;
      if (mode === 'absolute_floors') return `${num(a['value'])} plantas en total`;
      return `${signed(a['value'])} plantas`;
    }
    case 'object.move':
      return `Mover ${num(a['dxM'])} m al este y ${num(a['dyM'])} m al norte`;
    case 'object.rotate':
      return `Girar ${num(a['angleDeg'])}° en planta`;
    case 'object.duplicate':
      return `${num(a['copies'])} copia(s) a ${num(a['dxM'])} m E, ${num(a['dyM'])} m N`;
    case 'view.create': {
      const time = typeof a['time'] === 'string' ? (TIMES[a['time']] ?? a['time']) : null;
      const preset = PRESETS[String(a['preset'])] ?? '';
      return `Vista «${String(a['name'])}» ${preset}${time ? `, sol al ${time}` : ''}`;
    }
    case 'scene.set_sun':
      return `Sol: ${TIMES[String(a['time'])] ?? String(a['time'])}`;
    case 'site.place': {
      const asset = ASSETS[String(a['assetId'])] ?? String(a['assetId']);
      const layout =
        a['layout'] === 'row'
          ? `en fila${a['spacingM'] ? ` cada ${num(a['spacingM'])} m` : ''}`
          : a['layout'] === 'around'
            ? 'alrededor'
            : 'en un punto';
      const base = typeof a['baseZM'] === 'number' ? `, cota ${num(a['baseZM'])} m` : '';
      return `${num(a['count'])} × ${asset} ${layout}${base}`;
    }
    case 'object.delete':
      return 'Eliminar los elementos añadidos';
    case 'facade.add_windows':
    case 'facade.add_balconies': {
      const what = op.tool === 'facade.add_windows' ? 'Ventanas' : 'Balcones';
      const side = ORIENTATIONS[String(a['orientation'])] ?? String(a['orientation']);
      const floors = Array.isArray(a['floors']) ? ` · plantas ${a['floors'].join(', ')}` : '';
      return `${what} en la fachada ${side}${floors} (elementos añadidos, sin perforar)`;
    }
    case 'building.setback': {
      const sides = Array.isArray(a['orientations'])
        ? a['orientations'].map((o) => ORIENTATIONS[String(o)] ?? String(o)).join(', ')
        : '';
      return `Retranqueo de ${num(a['setbackM'])} m (${sides}) desde ${num(a['startHeightM'])} m, ${num(a['upperHeightM'])} m de alto`;
    }
    case 'building.raise_walls':
      return `Subir muros ${num(a['deltaM'])} m conservando la cubierta`;
    case 'roof.set_ridge':
      return `Cumbrera a ${num(a['ridgeHeightM'])} m sobre el alero`;
    case 'block.make_unique':
      return 'Independizar esta copia (las demás no cambian)';
    default:
      return op.title;
  }
}

export class EditSection {
  readonly element: HTMLElement;
  private readonly client: AiClient;
  private readonly gate = new RequestGate();
  private info: AiProviders | null = null;
  private job: string | null = null;
  private project: ProjectStatus | null = null;
  private plan: EditPlan | null = null;
  private planDescription = '';
  private selectedOps = new Set<string>();
  private resolutions: Record<string, string[]> = {};
  private inputs: OperationInputs = {};
  private results: ApplyResult | null = null;
  private previewState: ViewerState | null = null;
  private previewing = false;
  private session: { requests: number; limit: number } | null = null;
  private lastNote = '';
  private snapshot: { request: ProposalRequest; key: string; hash: string } | null = null;
  private limitRequest: ProposalRequest | null = null;
  /** Revisión confirmada cuyo modelo no se pudo cargar (para «Recargar modelo»). */
  private loadFailed: ProjectStatus | null = null;

  private readonly providerSelect = h('select', { class: 'select' });
  private readonly providerNote = h('p', { class: 'ai-note' });
  private readonly scopeSelect = h('select', { class: 'select' });
  private readonly description = h('textarea', {
    class: 'input ai-description',
    rows: 4,
    placeholder:
      'Ej.: Sube E-001 dos plantas, pon ladrillo claro en todos salvo E-004 y prepara una vista al atardecer.',
    spellcheck: true,
  });
  private readonly counter = h('span', { class: 'ai-counter mono' });
  private readonly proposeButton = button({
    label: 'Proponer cambios',
    icon: 'palette',
    variant: 'secondary',
    onClick: () => void this.propose(),
  });
  private readonly cancelButton = button({
    label: 'Cancelar',
    variant: 'ghost',
    onClick: () => this.cancel(),
  });
  private readonly reviewButton = button({
    label: 'Ver datos que se envían',
    icon: 'eye',
    variant: 'ghost',
    size: 'sm',
    onClick: () => void this.showConsent(false),
  });
  private readonly projectBar = h('div', { class: 'ai-project mat-row-actions' });
  private readonly consentBox = h('div', { class: 'ai-consent', attrs: { role: 'dialog' } });
  private readonly limitBox = h('div', { class: 'ai-consent' });
  private readonly usage = h('p', { class: 'ai-usage', attrs: { 'aria-live': 'polite' } });
  private readonly result = h('div', { class: 'ai-result' });

  constructor(
    private readonly host: EditHost,
    server: string,
  ) {
    this.client = new AiClient(server);
    const part = section('Editar proyecto con IA');
    this.element = part.element;
    this.cancelButton.hidden = true;
    this.consentBox.hidden = true;
    this.limitBox.hidden = true;
    part.body.append(
      this.projectBar,
      this.field('Proveedor', this.providerSelect),
      this.providerNote,
      this.field('Alcance', this.scopeSelect),
      h(
        'label',
        { class: 'field field-stack' },
        h('span', { class: 'field-label', textContent: 'Qué quieres cambiar' }),
        this.description,
        this.counter,
      ),
      h('p', {
        class: 'ai-note',
        textContent:
          'Solo se consulta al pulsar el botón. Materiales, altura, mover, girar, duplicar, vistas y sol se ejecutan de verdad; lo demás queda como pendiente. No escribas datos personales.',
      }),
      h(
        'div',
        { class: 'mat-row-actions' },
        this.proposeButton,
        this.cancelButton,
        this.reviewButton,
      ),
      this.consentBox,
      this.limitBox,
      this.usage,
      this.result,
      h(
        'div',
        { class: 'mat-row-actions' },
        button({
          label: 'Borrar caché de propuestas',
          variant: 'ghost',
          size: 'sm',
          onClick: () => void this.clearCache(),
        }),
        button({
          label: 'Revocar autorización',
          variant: 'ghost',
          size: 'sm',
          onClick: () => {
            consentStore.revoke(this.providerSelect.value);
            this.host.app.notify('Autorización revocada: se volverá a pedir.', 'info');
            this.renderProvider();
          },
        }),
      ),
    );
    // Cambiar lo que se enviaría invalida la autorización en curso (hay que revisarlo otra vez)
    const invalidate = (): void => {
      if (this.snapshot) {
        this.snapshot = null;
        this.consentBox.hidden = true;
      }
    };
    this.description.addEventListener('input', () => {
      invalidate();
      this.renderCounter();
    });
    this.providerSelect.addEventListener('change', () => {
      invalidate();
      this.renderProvider();
    });
    this.scopeSelect.addEventListener('change', () => {
      invalidate();
      this.render();
    });
  }

  private field(label: string, control: HTMLElement): HTMLElement {
    return h(
      'label',
      { class: 'field' },
      h('span', { class: 'field-label', textContent: label }),
      control,
    );
  }

  // --- Estado ---------------------------------------------------------------------------------

  async loadProviders(): Promise<void> {
    try {
      this.info = await this.client.providers();
    } catch {
      this.info = null;
    }
    const current = this.providerSelect.value;
    this.providerSelect.replaceChildren(
      ...(this.info?.providers ?? []).map((p) =>
        h('option', {
          value: p.id,
          textContent: p.available ? p.name : `${p.name} (no disponible)`,
          disabled: !p.available,
        }),
      ),
    );
    const first = this.info?.providers.find((p) => p.available);
    if (current && this.provider(current)?.available) this.providerSelect.value = current;
    else if (first) this.providerSelect.value = first.id;
    this.renderProvider();
  }

  private provider(id = this.providerSelect.value): AiProviderInfo | undefined {
    return this.info?.providers.find((p) => p.id === id);
  }

  /** Nuevo modelo: se descartan plan, autorización en curso y respuestas en vuelo. */
  reset(): void {
    this.gate.cancel();
    this.job = null;
    this.project = null;
    this.plan = null;
    this.results = null;
    this.previewing = false;
    this.previewState = null;
    this.snapshot = null;
    this.session = null;
    this.lastNote = '';
    this.consentBox.hidden = true;
    this.limitBox.hidden = true;
    this.host.app.highlight.clear();
    this.render();
  }

  /** Trabajo y revisión actual del proyecto. */
  async setJob(job: string): Promise<void> {
    const changed = job !== this.job;
    this.job = job;
    try {
      this.project = await this.client.project(job);
      if (changed) this.syncViewer(this.project);
    } catch {
      this.project = null;
    }
    this.render();
  }

  /**
   * Al abrir un proyecto: las vistas y el sol de las revisiones vigentes se reponen desde el
   * historial del servidor; las de revisiones deshechas se quitan (no dependen del navegador).
   */
  private syncViewer(status: ProjectStatus): void {
    const byId = new Map(status.history.map((r) => [r.id, r]));
    const chain = new Set<string>();
    for (let id: string | null = status.revision; id; id = byId.get(id)?.parent ?? null) {
      chain.add(id);
    }
    let last: ViewerEffects | null = null;
    for (const revision of status.history) {
      if (!revision.viewer) continue;
      if (chain.has(revision.id)) {
        redoEffects(this.host.app, this.host.views, revision.viewer);
        if (revision.viewer.sunAfter) last = revision.viewer;
      } else {
        for (const view of revision.viewer.views) this.host.views.remove(view.id);
      }
    }
    if (last?.sunAfter) {
      this.host.app.setState({ ...this.host.app.snapshot(), sun: last.sunAfter }, false);
    }
  }

  /** Operaciones del visor marcadas, con sus objetivos finales (dudas ya resueltas). */
  private viewerOps(): {
    id: string;
    tool: string;
    args: Record<string, unknown>;
    objectIds: string[];
  }[] {
    const plan = this.plan;
    if (!plan) return [];
    return plan.operations
      .filter((o) => this.selectedOps.has(o.id) && o.executor === 'viewer')
      .map((o) => ({
        id: o.id,
        tool: o.tool,
        args: o.args,
        objectIds: (o.pending ? this.resolutions[o.pending] : undefined) ?? o.objectIds,
      }));
  }

  get revision(): string | null {
    return this.project?.revision ?? null;
  }

  /** La pestaña confirmó materiales a mano: el proyecto avanzó de revisión. */
  async refreshProject(): Promise<void> {
    if (this.job) await this.setJob(this.job);
  }

  // --- Solicitud ------------------------------------------------------------------------------

  private scope(): { scope: AiScope; layers: string[] } {
    const value = this.scopeSelect.value;
    if (value === 'selection') return { scope: 'selection', layers: [] };
    if (value.startsWith('layer:')) return { scope: 'layers', layers: [value.slice(6)] };
    return { scope: 'all', layers: [] };
  }

  private request(consent: boolean, continueAfterLimit = false): ProposalRequest {
    const { scope, layers } = this.scope();
    return {
      provider: this.providerSelect.value,
      description: this.description.value,
      scope,
      selection: [...this.host.selected()].sort(),
      layers,
      baseRevision: this.revision,
      consent,
      continueAfterLimit,
    };
  }

  private check(): string | null {
    const provider = this.provider();
    const text = this.description.value.trim();
    const max = this.info?.limits.maxDescriptionChars ?? 4000;
    if (!this.host.usable()) return 'Abre un GLB base con objetos editables.';
    if (!provider) return 'No hay proveedores de IA: revisa extrude serve.';
    if (!provider.available) return provider.reason ?? `${provider.name} no está disponible.`;
    if (!text) return 'Escribe qué quieres cambiar.';
    if (text.length > max) return `La petición supera ${max} caracteres.`;
    if (this.scope().scope === 'selection' && !this.host.selected().size) {
      return 'Marca algún objeto o cambia el alcance.';
    }
    return null;
  }

  private async propose(): Promise<void> {
    if (this.gate.busy) return; // sin dobles envíos
    const problem = this.check();
    if (problem) {
      this.host.app.notify(problem, 'warning');
      return;
    }
    if (!consentStore.has(this.providerSelect.value)) {
      await this.showConsent(true);
      return;
    }
    await this.ensureJob();
    await this.send(this.request(true));
  }

  /** Muestra proveedor, datos y contenido exacto; autorizar envía ESA solicitud (y su huella). */
  private async showConsent(thenSend: boolean): Promise<void> {
    const problem = this.check();
    if (problem) {
      this.host.app.notify(problem, 'warning');
      return;
    }
    const provider = this.provider()!;
    let preview: ContextPreview;
    let request: ProposalRequest;
    try {
      const job = await this.ensureJob();
      request = this.request(false);
      preview = await this.client.context(job, request);
    } catch (error) {
      this.fail(error);
      return;
    }
    this.snapshot = { request, key: requestKey(request), hash: preview.contextHash };
    const where = provider.simulated
      ? 'El proveedor simulado se ejecuta en extrude serve: los datos no salen del equipo.'
      : `Se enviará a ${provider.name} (modelo ${provider.model ?? '—'}) desde extrude serve.`;
    const authorize = button({
      label: 'Autorizar y enviar',
      icon: 'check',
      variant: 'primary',
      onClick: () => {
        const snapshot = this.snapshot;
        // Solo lo revisado: si algo cambió desde que se mostró, se revisa otra vez
        if (!snapshot || snapshot.key !== requestKey(this.request(false))) {
          this.consentBox.hidden = true;
          this.host.app.notify('La solicitud cambió: revísala de nuevo.', 'warning');
          return;
        }
        consentStore.grant(snapshot.request.provider);
        this.consentBox.hidden = true;
        this.snapshot = null;
        void this.send({ ...snapshot.request, consent: true, consentHash: snapshot.hash });
      },
    });
    this.consentBox.replaceChildren(
      ...present(
        h('h4', { textContent: thenSend ? `¿Enviar a ${provider.name}?` : 'Datos que se envían' }),
        h('p', { textContent: where }),
        h(
          'ul',
          {},
          h('li', { textContent: 'Tu petición, tal como la escribes.' }),
          h('li', {
            textContent: `${preview.objectsSent} de ${preview.objectsInModel} objetos: etiqueta, capa, tipo, bloque, tamaño, altura, altura de planta, giro, superficies, materiales actuales y posición relativa en metros.`,
          }),
          h('li', {
            textContent: `Revisión del proyecto ${preview.baseRevision}, herramientas disponibles, materiales de la biblioteca y tus asignaciones manuales del alcance.`,
          }),
        ),
        h('p', { class: 'ai-note', textContent: `No se envía: ${preview.notSent.join(', ')}.` }),
        h('p', {
          class: 'ai-note',
          textContent: `≈${preview.estimatedInputTokens.toLocaleString('es')} tokens de entrada (estimación), máximo ${preview.maxOutputTokens.toLocaleString('es')} de salida.${preview.cached ? ' Ya está en caché: no se consultará al proveedor.' : ''} El texto libre puede contener datos sensibles: no se anonimiza.`,
        }),
        h(
          'details',
          {},
          h('summary', { textContent: 'Contenido exacto del mensaje' }),
          h('pre', {
            class: 'ai-preview mono',
            textContent: JSON.stringify(preview.user, null, 2),
          }),
          h('pre', { class: 'ai-preview mono', textContent: preview.system }),
        ),
        h(
          'div',
          { class: 'mat-row-actions' },
          thenSend ? authorize : null,
          button({
            label: thenSend ? 'No enviar' : 'Cerrar',
            variant: 'ghost',
            onClick: () => {
              this.consentBox.hidden = true;
              this.snapshot = null;
            },
          }),
        ),
      ),
    );
    this.consentBox.hidden = false;
  }

  private async ensureJob(): Promise<string> {
    const job = await this.host.ensureJob();
    if (job !== this.job || !this.project) await this.setJob(job);
    return job;
  }

  private async send(request: ProposalRequest): Promise<void> {
    if (this.gate.busy) return;
    this.limitBox.hidden = true;
    const { turn, signal } = this.gate.start();
    this.lastNote = 'Consultando… (una sola llamada)';
    this.render();
    try {
      const job = await this.ensureJob();
      if (!this.gate.current(turn)) return;
      const plan = await this.client.propose(job, request, signal);
      if (!this.gate.current(turn)) return; // respuesta tardía: se descarta
      if (!planIsCurrent(plan, this.revision)) {
        this.host.app.notify(
          'La propuesta es de otra revisión del proyecto: se descarta.',
          'warning',
        );
        return;
      }
      this.plan = plan;
      this.planDescription = request.description;
      this.selectedOps = defaultSelection(plan);
      this.resolutions = {};
      this.inputs = {};
      this.results = null;
      this.session = plan.session;
      this.lastNote = this.describeUsage(plan);
      this.host.app.highlightObjects(plan.operations.flatMap((o) => o.objectIds));
    } catch (error) {
      if (!this.gate.current(turn)) return;
      if (error instanceof AiRequestError && error.kind === 'session_limit') {
        this.limitRequest = request;
        this.showLimit(error);
      } else {
        this.fail(error);
      }
      const session = error instanceof AiRequestError ? error.details['session'] : undefined;
      if (session) this.session = session as { requests: number; limit: number };
    } finally {
      this.gate.finish(turn);
      this.render();
    }
  }

  private cancel(): void {
    if (!this.gate.busy) return;
    this.gate.cancel();
    this.lastNote =
      'Cancelada. Cancelar la espera no detiene el cálculo del proveedor (puede facturarlo); si termina, quedará en caché y repetir la misma petición no volverá a consultar.';
    this.render();
  }

  private showLimit(error: AiRequestError): void {
    this.limitBox.replaceChildren(
      h('h4', { textContent: 'Límite de consultas de la sesión' }),
      h('p', { textContent: `${error.message} ${error.fix}` }),
      h(
        'div',
        { class: 'mat-row-actions' },
        button({
          label: 'Continuar con una consulta más',
          variant: 'secondary',
          onClick: () => {
            const request = this.limitRequest;
            this.limitBox.hidden = true;
            if (request) void this.send({ ...request, continueAfterLimit: true });
          },
        }),
        button({ label: 'No', variant: 'ghost', onClick: () => (this.limitBox.hidden = true) }),
      ),
    );
    this.limitBox.hidden = false;
  }

  private fail(error: unknown): void {
    if (error instanceof DOMException && error.name === 'AbortError') return;
    const message =
      error instanceof ConversionError ? `${error.message} ${error.fix}` : String(error);
    this.lastNote = message;
    this.host.app.notify(message, 'error');
  }

  private describeUsage(plan: EditPlan): string {
    if (plan.cached) return 'Propuesta reutilizada de la caché: no se ha consultado al proveedor.';
    const parts = [`${plan.provider} · ${plan.model}`];
    if (plan.durationMs !== null) parts.push(`${(plan.durationMs / 1000).toFixed(1)} s`);
    const usage = plan.usage;
    if (usage?.inputTokens != null && usage.outputTokens != null) {
      parts.push(
        `${usage.inputTokens.toLocaleString('es')} tokens de entrada / ${usage.outputTokens.toLocaleString('es')} de salida (informado por la API)`,
      );
    } else {
      parts.push('consumo no informado');
    }
    if (plan.costEstimate) {
      parts.push(
        `≈${plan.costEstimate.amount.toFixed(4)} ${plan.costEstimate.currency} según EXTRUDE_AI_PRICES`,
      );
    }
    return parts.join(' · ');
  }

  private async clearCache(): Promise<void> {
    try {
      const { removed } = await this.client.clearCache(await this.ensureJob());
      this.host.app.notify(`Caché de propuestas borrada (${removed}).`, 'info');
    } catch (error) {
      this.fail(error);
    }
  }

  // --- Aplicar --------------------------------------------------------------------------------

  private body(): ApplyBody | null {
    const plan = this.plan;
    if (!plan || !this.project) return null;
    return {
      planId: plan.planId,
      baseRevision: this.project.revision,
      operations: plan.operations.filter((o) => this.selectedOps.has(o.id)).map((o) => o.id),
      resolutions: this.resolutions,
      inputs: this.inputs,
      description: this.planDescription.slice(0, 10_000),
    };
  }

  private blockers(): string | null {
    const plan = this.plan;
    if (!plan) return 'No hay plan.';
    if (!planIsCurrent(plan, this.revision)) {
      return `El plan se hizo sobre ${plan.baseRevision} y el proyecto está en ${this.revision ?? '—'}: pídelo de nuevo.`;
    }
    if (this.host.manualDirty()) {
      return 'Confirma o descarta antes tus asignaciones manuales pendientes.';
    }
    const chosen = plan.operations.filter((o) => this.selectedOps.has(o.id));
    if (!chosen.length) return 'Marca al menos una operación.';
    const waiting = chosen.filter((o) => !applicable(o, this.resolutions, this.inputs));
    if (waiting.length) {
      return `Resuelve antes ${waiting.map((o) => o.id).join(', ')} (elige objetos o completa el dato).`;
    }
    return null;
  }

  private async apply(preview: boolean): Promise<void> {
    const problem = this.blockers();
    const body = this.body();
    if (problem || !body || this.gate.busy) {
      if (problem) this.host.app.notify(problem, 'warning');
      return;
    }
    // Fase 1 del visor: vistas y sol se calculan y validan ANTES de tocar el servidor. Si no
    // son posibles (p. ej. «atardecer» sin puesta de sol), no se confirma nada.
    let prepared: ViewerEffects | null = null;
    const viewerOps = this.viewerOps();
    // Una vista que encuadra objetos movidos/editados en el mismo plan se calcula sobre la
    // geometría RESULTANTE: se previsualiza antes (sin IA) y se prepara sobre esa previsualización.
    const geometry = this.plan?.operations.some(
      (o) => this.selectedOps.has(o.id) && o.executor === 'server' && o.tool !== 'material.assign',
    );
    if (!preview && !this.previewing && geometry && viewerOps.some((o) => o.objectIds.length)) {
      try {
        const job = await this.ensureJob();
        const shown = await this.client.apply(job, body, true); // aún sin estado del visor
        if (shown.url) await this.host.showPreview(shown.url);
        this.previewing = true;
      } catch (error) {
        this.fail(error);
        return;
      }
    }
    if (viewerOps.length) {
      try {
        if (this.previewState) restore(this.host.app, this.previewState);
        prepared = prepareViewerOps(this.host.app, viewerOps);
      } catch (error) {
        this.host.app.notify(
          `No se puede preparar la vista o el sol: ${error instanceof Error ? error.message : String(error)}`,
          'error',
        );
        return;
      }
      body.viewer = prepared;
    }
    const { turn, signal } = this.gate.start();
    this.render();
    try {
      const job = await this.ensureJob();
      const result = await this.client.apply(job, body, preview, signal);
      if (!this.gate.current(turn)) return;
      this.results = result;
      if (preview) {
        if (result.url) await this.host.showPreview(result.url);
        if (prepared) {
          const before = previewPrepared(this.host.app, prepared);
          this.previewState ??= before;
        }
        this.previewing = true;
        this.host.app.notify('Previsualización sin confirmar.', 'info');
      } else {
        if (this.previewState) restore(this.host.app, this.previewState);
        this.previewState = null;
        this.previewing = false;
        if (result.project) {
          const confirmed = result.project;
          this.project = confirmed;
          if (prepared) await this.commitOrCompensate(job, confirmed.revision, prepared);
          try {
            await this.host.applyProject(confirmed, result.plan);
            this.loadFailed = null;
          } catch (error) {
            // Confirmado en el servidor pero sin cargar aquí: se dice y se puede reintentar
            this.loadFailed = confirmed;
            throw new AiRequestError(
              `Cambios confirmados en el servidor (${confirmed.revision}), pero no se pudo cargar el modelo: ${error instanceof Error ? error.message : String(error)}`,
              'Pulsa «Recargar modelo». Nada se ha perdido.',
              'load_failed',
            );
          }
        }
        this.plan = null; // confirmado: el plan queda registrado en la revisión
        this.host.app.notify(`Cambios confirmados (${result.revision ?? ''}).`, 'success');
      }
      if (result.warnings.length) this.host.app.notify(result.warnings.join(' '), 'warning');
    } catch (error) {
      if (!this.gate.current(turn)) return;
      if (error instanceof AiRequestError && Array.isArray(error.details['results'])) {
        this.results = {
          preview,
          results: error.details['results'] as OperationResult[],
          viewerOps: [],
          warnings: [],
          geometryChanged: false,
        };
      }
      if (error instanceof AiRequestError && error.kind === 'stale_plan') {
        await this.refreshProject();
      }
      this.fail(error);
    } finally {
      this.gate.finish(turn);
      this.render();
    }
  }

  /**
   * Fase 2 del visor, tras confirmar en el servidor. Si aun así fallara, se deshace la revisión
   * recién creada: el proyecto no queda confirmado a medias.
   */
  private async commitOrCompensate(
    job: string,
    revision: string,
    prepared: ViewerEffects,
  ): Promise<void> {
    try {
      commitPrepared(this.host.app, this.host.views, prepared);
    } catch (error) {
      undoEffects(this.host.app, this.host.views, prepared);
      // Solo esta revisión: si otra llegó después, el servidor se niega (409) y no se deshace
      const status = await this.client.step(job, false, revision);
      this.project = status;
      await this.host.applyProject(status, undefined);
      throw new AiRequestError(
        `No se pudieron aplicar las vistas o el sol (${error instanceof Error ? error.message : String(error)}); se ha deshecho la revisión.`,
        'Revisa la petición y vuelve a confirmar.',
        'viewer_failed',
      );
    }
  }

  private async discard(): Promise<void> {
    if (this.previewState) restore(this.host.app, this.previewState);
    this.previewState = null;
    if (this.previewing) await this.host.discardPreview();
    this.previewing = false;
    this.render();
  }

  private async step(forward: boolean): Promise<void> {
    if (!this.job || this.gate.busy) return;
    const leaving = this.project?.history.find((r) => r.id === this.project?.revision);
    try {
      if (this.previewing) await this.discard();
      const status = await this.client.step(this.job, forward);
      this.project = status;
      await this.host.applyProject(status, undefined);
      // Estado del visor guardado con cada revisión (persistente, también tras reabrir)
      const arrived = status.history.find((r) => r.id === status.revision);
      if (!forward && leaving?.viewer) undoEffects(this.host.app, this.host.views, leaving.viewer);
      if (forward && arrived?.viewer) redoEffects(this.host.app, this.host.views, arrived.viewer);
      this.host.app.notify(
        `${forward ? 'Rehecho' : 'Deshecho'}: proyecto en ${status.revision}.`,
        'info',
      );
    } catch (error) {
      this.fail(error);
    }
    this.render();
  }

  // --- Render ---------------------------------------------------------------------------------

  private renderCounter(): void {
    const max = this.info?.limits.maxDescriptionChars ?? 4000;
    const length = this.description.value.length;
    this.counter.textContent = `${length.toLocaleString('es')} / ${max.toLocaleString('es')}`;
    this.counter.classList.toggle('is-over', length > max);
  }

  private renderProvider(): void {
    const provider = this.provider();
    if (!this.info) {
      this.providerNote.textContent =
        'Sin conexión con extrude serve: el formulario manual funciona cuando arranque.';
      return;
    }
    if (!provider) {
      this.providerNote.textContent = 'No hay proveedores configurados (docs/EDICION_IA.md).';
      return;
    }
    const notes = [
      provider.simulated
        ? 'Reglas locales para probar el flujo: no es IA ni sale nada del equipo.'
        : `Modelo ${provider.model ?? '—'}.`,
    ];
    if (!provider.available && provider.reason) notes.push(provider.reason);
    if (provider.limitsNote) notes.push(provider.limitsNote);
    if (consentStore.has(provider.id)) notes.push('Envío autorizado.');
    this.providerNote.textContent = notes.join(' ');
  }

  private renderScope(): void {
    const current = this.scopeSelect.value || 'all';
    const layers = [...new Set([...this.host.catalog().values()].map((o) => o.layer))].sort();
    this.scopeSelect.replaceChildren(
      h('option', { value: 'all', textContent: `Todo el modelo (${this.host.catalog().size})` }),
      h('option', {
        value: 'selection',
        textContent: `Solo marcados (${this.host.selected().size})`,
      }),
      ...layers.map((layer) =>
        h('option', { value: `layer:${layer}`, textContent: `Capa ${layer}` }),
      ),
    );
    this.scopeSelect.value = [...this.scopeSelect.options].some((o) => o.value === current)
      ? current
      : 'all';
  }

  private renderProject(): void {
    const project = this.project;
    if (!project) {
      this.projectBar.replaceChildren(
        h('span', { class: 'ai-note', textContent: 'Proyecto sin revisiones todavía.' }),
      );
      return;
    }
    const last = project.history.find((r) => r.id === project.revision);
    const undo = button({
      label: 'Deshacer',
      variant: 'ghost',
      size: 'sm',
      onClick: () => void this.step(false),
    });
    const redo = button({
      label: 'Rehacer',
      variant: 'ghost',
      size: 'sm',
      onClick: () => void this.step(true),
    });
    undo.disabled = !project.canUndo || this.gate.busy;
    redo.disabled = !project.canRedo || this.gate.busy;
    const failed = this.loadFailed;
    const reload = failed
      ? button({
          label: 'Recargar modelo',
          variant: 'secondary',
          size: 'sm',
          onClick: () => {
            void this.host
              .applyProject(failed, undefined)
              .then(() => {
                this.loadFailed = null;
                this.host.app.notify(`Modelo de ${failed.revision} cargado.`, 'success');
              })
              .catch((error: unknown) => this.fail(error))
              .finally(() => this.render());
          },
        })
      : null;
    this.projectBar.replaceChildren(
      ...present(
        h('span', {
          class: 'mono ai-revision',
          textContent: `Revisión ${project.revision}${failed ? ' · sin cargar' : ''}`,
          title: last?.summary ?? '',
        }),
        undo,
        redo,
        reload,
      ),
    );
  }

  private labels(ids: string[]): string {
    const catalog = this.host.catalog();
    const labels = ids.map((id) => catalog.get(id)?.label ?? id);
    if (labels.length <= SUMMARY) return labels.join(', ');
    return `${labels.slice(0, SUMMARY).join(', ')} +${labels.length - SUMMARY}`;
  }

  private readonly materialName = (id: string): string =>
    this.host.library()?.materials.find((m) => m.id === id)?.name ?? id;

  private renderInput(op: PlanOperation): HTMLElement | null {
    if (op.status !== 'needs_input') return null;
    const rows = neededInputs(op).map((need) => {
      const current = this.inputs[op.id]?.[need.key];
      const field = h('input', {
        type: 'number',
        class: 'input input-number',
        min: String(need.min),
        max: String(need.max),
        step: '0.1',
        value: current === undefined ? '' : String(current),
        placeholder: need.unit,
      });
      field.addEventListener('change', () => {
        const value = field.value.trim() === '' ? NaN : Number(field.value);
        const mine = { ...(this.inputs[op.id] ?? {}) };
        if (Number.isFinite(value) && value >= need.min && value <= need.max)
          mine[need.key] = value;
        else delete mine[need.key];
        const next = { ...this.inputs };
        if (Object.keys(mine).length) next[op.id] = mine;
        else delete next[op.id];
        this.inputs = next;
        this.render();
      });
      return h(
        'label',
        { class: 'mat-inline' },
        h('span', { textContent: `${need.label} (${need.unit})` }),
        field,
      );
    });
    return h('div', { class: 'mat-inputs' }, ...rows);
  }

  private renderOperation(plan: EditPlan, op: PlanOperation): HTMLElement {
    const ok = applicable(op, this.resolutions, this.inputs);
    const box = h('input', {
      type: 'checkbox',
      checked: this.selectedOps.has(op.id),
      disabled: !['ready', 'needs_resolution', 'needs_input'].includes(op.status),
      title: op.dependsOn.length ? `Depende de ${op.dependsOn.join(', ')}` : '',
    });
    box.addEventListener('change', () => {
      this.selectedOps = toggleOperation(plan, this.selectedOps, op.id, box.checked);
      this.render();
    });
    const resolved = op.pending ? this.resolutions[op.pending] : undefined;
    const targets = resolved ?? op.objectIds;
    const status = ok && op.status !== 'ready' ? 'Resuelta' : (STATUS_TEXT[op.status] ?? op.status);
    const heights = op.impact.heights
      ?.map((c) => `${c.label} ${num(c.from)} → ${c.to === null ? '?' : num(c.to)} m`)
      .join(' · ');
    const row = h(
      'li',
      {
        class: `mat-assignment ai-row is-${op.status}${op.basis === 'assumption' ? ' is-assumption' : ''}`,
      },
      box,
      h(
        'div',
        { class: 'mat-assignment-text' },
        h(
          'strong',
          {},
          `${op.id} · ${describeOperation(op, this.materialName)}`,
          op.basis === 'assumption'
            ? h('em', { class: 'ai-badge', textContent: 'supuesto' })
            : null,
          h('em', { class: `ai-status is-${op.status}`, textContent: status }),
        ),
        targets.length
          ? h('span', { textContent: this.labels(targets), title: targets.join(', ') })
          : null,
        heights ? h('span', { class: 'mono', textContent: heights }) : null,
        op.dependsOn.length
          ? h('span', { class: 'ai-note', textContent: `Después de ${op.dependsOn.join(', ')}` })
          : null,
        h('span', { class: 'ai-reason', textContent: op.reason }),
        ...op.messages.map((m) => h('span', { class: 'mat-issue-inline', textContent: m })),
        op.incompatible.length
          ? h('span', {
              class: 'ai-note',
              textContent: `No compatibles: ${op.incompatible.map((i) => `${i.label} (${i.reason})`).join('; ')}`,
            })
          : null,
        this.renderInput(op),
      ),
      button({
        icon: 'eye',
        title: 'Resaltar en el modelo',
        variant: 'ghost',
        size: 'sm',
        onClick: () => this.host.app.highlightObjects(targets),
      }),
    );
    row.addEventListener('mouseenter', () => this.host.app.highlightObjects(targets));
    return row;
  }

  private renderPending(plan: EditPlan): HTMLElement | null {
    if (!plan.pending.length) return null;
    const items = plan.pending.map((p) => {
      const chosen = this.resolutions[p.id];
      const use = (ids: string[]): void => {
        this.resolutions = { ...this.resolutions, [p.id]: ids };
        this.host.mark(ids);
        this.host.app.highlightObjects(ids);
        this.render();
      };
      return h(
        'li',
        {},
        h('strong', { textContent: p.reference }),
        h('span', { textContent: ` — ${p.question}` }),
        p.candidateObjectIds.length
          ? h('div', {
              class: 'ai-note',
              textContent: `Candidatos${p.rule ? ' (regla explícita)' : ''}: ${this.labels(p.candidateObjectIds)}`,
            })
          : null,
        chosen
          ? h('div', { class: 'ai-note', textContent: `Elegidos: ${this.labels(chosen)}` })
          : null,
        h(
          'div',
          { class: 'mat-row-actions' },
          p.candidateObjectIds.length
            ? button({
                label: 'Usar candidatos',
                variant: 'ghost',
                size: 'sm',
                onClick: () => use(p.candidateObjectIds),
              })
            : null,
          button({
            label: `Usar marcados (${this.host.selected().size})`,
            variant: 'ghost',
            size: 'sm',
            onClick: () => {
              if (!this.host.selected().size) {
                this.host.app.notify('Marca objetos en la lista o en la vista.', 'warning');
                return;
              }
              use([...this.host.selected()]);
            },
          }),
        ),
      );
    });
    return h(
      'div',
      { class: 'ai-block is-warning' },
      h('h4', { textContent: 'Pendiente de aclarar (sin volver a consultar)' }),
      h('ul', {}, ...items),
    );
  }

  private renderResults(): HTMLElement | null {
    const results = this.results;
    if (!results) return null;
    return h(
      'div',
      { class: 'ai-block' },
      h('h4', { textContent: results.preview ? 'Previsualización' : 'Resultado' }),
      h(
        'ul',
        {},
        ...results.results.map((r) =>
          h('li', {
            class: `ai-result-${r.status}`,
            textContent: `${r.id}: ${RESULT_TEXT[r.status]}${r.message ? ` — ${r.message}` : ''}`,
          }),
        ),
        ...results.warnings.map((w) => h('li', { class: 'ai-note', textContent: w })),
      ),
    );
  }

  private renderPlan(): void {
    const plan = this.plan;
    if (!plan) {
      this.result.replaceChildren(...present(this.renderResults()));
      return;
    }
    const stale = !planIsCurrent(plan, this.revision);
    const blocker = this.blockers();
    const busy = this.gate.busy;
    const previewButton = button({
      label: 'Previsualizar marcadas',
      icon: 'eye',
      variant: 'secondary',
      onClick: () => void this.apply(true),
    });
    const confirmButton = button({
      label: 'Confirmar',
      icon: 'check',
      variant: 'primary',
      onClick: () => void this.apply(false),
    });
    previewButton.disabled = Boolean(blocker) || busy;
    confirmButton.disabled = Boolean(blocker) || busy;
    previewButton.title = blocker ?? '';
    confirmButton.title = blocker ?? '';
    const operations = plan.operations.map((o) => this.renderOperation(plan, o));
    this.result.replaceChildren(
      ...present(
        stale
          ? h('p', {
              class: 'mat-issue',
              textContent: `Plan obsoleto: se hizo sobre ${plan.baseRevision} y el proyecto está en ${this.revision ?? '—'}. Vuelve a pedirlo.`,
            })
          : null,
        h(
          'div',
          { class: 'ai-block ai-understood' },
          h('h4', { textContent: 'Así he entendido tu petición' }),
          h('p', { textContent: plan.summary }),
        ),
        list('Requisitos explícitos', plan.explicitRequirements),
        list('Supuestos (decisiones propuestas: revísalas)', plan.assumptions, 'is-warning'),
        h(
          'div',
          { class: 'ai-block' },
          h('h4', { textContent: `Operaciones (${plan.operations.length})` }),
          operations.length
            ? h('ul', { class: 'mat-assignments' }, ...operations)
            : h('p', { class: 'ai-note', textContent: 'Ninguna ejecutable.' }),
        ),
        this.renderPending(plan),
        list(
          'Conflictos',
          plan.conflicts.map((c) => c.message),
          'is-danger',
        ),
        list(
          'Aún no se puede hacer (capacidad pendiente)',
          plan.unsupported.map((u) => `${u.request} — ${u.capabilityTitle}: ${u.reason}`),
        ),
        this.renderResults(),
        blocker && !stale ? h('p', { class: 'ai-note', textContent: blocker }) : null,
        h(
          'div',
          { class: 'mat-row-actions' },
          previewButton,
          confirmButton,
          button({
            label: this.previewing ? 'Descartar previsualización' : 'Descartar propuesta',
            variant: 'ghost',
            onClick: () => {
              if (this.previewing) {
                void this.discard();
              } else {
                this.plan = null;
                this.host.app.highlight.clear();
                this.render();
              }
            },
          }),
        ),
      ),
    );
  }

  render(): void {
    const busy = this.gate.busy;
    this.renderScope();
    this.renderCounter();
    this.renderProject();
    const label = this.proposeButton.querySelector('span');
    if (label) label.textContent = this.plan ? 'Actualizar propuesta' : 'Proponer cambios';
    const max = this.info?.limits.maxDescriptionChars ?? 4000;
    this.proposeButton.disabled =
      busy || !this.host.usable() || this.description.value.length > max;
    this.cancelButton.hidden = !busy;
    this.reviewButton.disabled = busy || !this.host.usable();
    const count = this.session
      ? `Consultas en este proyecto: ${this.session.requests} de ${this.session.limit} antes de confirmar más`
      : 'Consultas en este proyecto: 0';
    this.usage.textContent = this.lastNote ? `${count}. ${this.lastNote}` : count;
    this.renderPlan();
  }
}
