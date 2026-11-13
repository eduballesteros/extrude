/**
 * Generar una imagen arquitectónica a partir de vistas guardadas.
 *
 * El prompt libre del usuario es la instrucción principal. Antes de enviar nada se muestra qué
 * capturas se envían, a qué proveedor y con qué texto exacto; la generación se lanza con un
 * botón explícito. El resultado se guarda en la biblioteca junto a su captura original, con el
 * prompt, el proveedor y las dimensiones reales del archivo recibido.
 */
import { blobToBase64, referenceImage } from '../library/bitmap';
import {
  GenerationCancelled,
  GenerationError,
  type GenerationJob,
  type ImageGenClient,
  type ImageProviderInfo,
  type ImageProviders,
  type SizePlan,
  type Target,
  TARGET_LABEL,
} from '../library/generation';
import type { LibraryImage } from '../library/images';
import type { SceneView } from '../views/viewState';
import { button, h, icon } from './dom';
import type { LibraryActions } from './imageLibrary';

/** Sugerencias opcionales: se añaden al prompt al pulsarlas; nunca se envían solas. */
const SUGGESTIONS = [
  'visualización arquitectónica fotorrealista',
  'materiales claros (piedra clara, hormigón blanco, madera)',
  'vegetación abundante y arbolado maduro',
  'luz natural suave de tarde',
  'contexto paisajístico con colinas al fondo',
];

/** Frase opcional de fidelidad: se muestra entera y el usuario decide si se añade. */
export const FIDELITY_SENTENCE =
  'Conserva exactamente el encuadre, el punto de vista, la volumetría, la distribución y las relaciones espaciales de la imagen de referencia; no añadas, quites ni muevas edificios.';

type Phase = 'edit' | 'review' | 'running' | 'done';

interface PreparedSource {
  view: SceneView;
  capture: LibraryImage;
  /** Lo que se envía: la captura, reducida si pasa de 2048 px de lado. */
  sent: { data: string; width: number; height: number };
}

function message(error: unknown): string {
  if (error instanceof GenerationError) return `${error.message} ${error.fix}`.trim();
  return error instanceof Error ? error.message : String(error);
}

/** Prompt que se envía: el texto del usuario y, si lo marca, la frase de fidelidad. */
export function finalPrompt(text: string, keepGeometry: boolean): string {
  const prompt = text.trim();
  return keepGeometry && prompt ? `${prompt}\n\n${FIDELITY_SENTENCE}` : prompt;
}

export class GenerateDialog {
  readonly element: HTMLElement;
  private readonly body = h('div', { class: 'generate-body' });
  private readonly footer = h('div', { class: 'generate-footer' });
  private info: ImageProviders | null = null;
  private infoError: string | null = null;
  private phase: Phase = 'edit';
  private selected: string[] = [];
  private prompt = '';
  private keepGeometry = true;
  private provider = '';
  private model = '';
  private target: Target = 'hd';
  private plans: SizePlan[] = [];
  private planError: string | null = null;
  private planToken = 0;
  private suggestion: string | null = null;
  private suggesting = false;
  private error: string | null = null;
  private sources: PreparedSource[] = [];
  /** Identifica la solicitud: se conserva en un reintento que el servidor pudo haber recibido. */
  private requestId = crypto.randomUUID();
  private abort: AbortController | null = null;
  private job: GenerationJob | null = null;
  private startedAt = 0;
  private ticker: ReturnType<typeof setInterval> | null = null;
  private result: LibraryImage | null = null;

  constructor(
    private readonly actions: LibraryActions,
    private readonly client: ImageGenClient,
  ) {
    this.element = h(
      'div',
      {
        class: 'library-overlay generate-overlay',
        hidden: true,
        attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Generar imagen con IA' },
      },
      h(
        'div',
        { class: 'library-card generate-card' },
        h(
          'header',
          { class: 'library-header' },
          h(
            'div',
            { class: 'library-heading' },
            h('h2', { class: 'library-title', textContent: 'Generar imagen con IA' }),
            h('span', {
              class: 'library-summary',
              textContent: 'A partir de vistas guardadas y de tu prompt',
            }),
          ),
          button({
            label: 'Cerrar',
            icon: 'close',
            variant: 'ghost',
            size: 'sm',
            onClick: () => this.close(),
          }),
        ),
        this.body,
        this.footer,
      ),
    );
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && this.visible && !actions.viewer.visible) {
        event.stopImmediatePropagation();
        this.close();
      }
    });
    document.body.append(this.element);
  }

  get visible(): boolean {
    return !this.element.hidden;
  }

  private get views(): SceneView[] {
    return this.actions.ctx.views.views;
  }

  open(viewId?: string): void {
    if (this.phase === 'done') this.reset();
    const known = new Set(this.views.map((view) => view.id));
    this.selected = this.selected.filter((id) => known.has(id));
    if (viewId && known.has(viewId) && !this.selected.includes(viewId)) {
      this.selected = [viewId, ...this.selected].slice(0, this.maxReferences);
    }
    if (this.selected.length === 0 && this.views[0]) this.selected = [this.views[0].id];
    this.element.hidden = false;
    this.render();
    void this.loadProviders();
  }

  /** Cerrar no cancela una generación en curso: sigue y se guarda al terminar. */
  close(): void {
    this.element.hidden = true;
  }

  private reset(): void {
    this.phase = 'edit';
    this.error = null;
    this.result = null;
    this.job = null;
    this.sources = [];
    this.requestId = crypto.randomUUID();
  }

  private get maxReferences(): number {
    return this.info?.limits.maxReferences ?? 4;
  }

  private get maxPrompt(): number {
    return this.info?.limits.maxPromptChars ?? 4000;
  }

  private providerInfo(): ImageProviderInfo | undefined {
    return this.info?.providers.find((provider) => provider.id === this.provider);
  }

  private async loadProviders(): Promise<void> {
    try {
      this.info = await this.client.providers();
      this.infoError = null;
      if (!this.providerInfo()?.available) {
        const first = this.info.providers.find((provider) => provider.available);
        this.provider = (first ?? this.info.providers[0])?.id ?? '';
        this.model = this.providerInfo()?.models[0] ?? '';
      }
    } catch (error) {
      this.info = null;
      this.infoError = message(error);
    }
    this.render();
    void this.loadPlans();
  }

  /** Resolución que se pediría para cada objetivo, según el proveedor, el modelo y la vista. */
  private async loadPlans(): Promise<void> {
    const view = this.views.find((v) => v.id === this.selected[0]);
    const token = ++this.planToken;
    this.plans = [];
    this.planError = null;
    if (!view || !this.providerInfo()?.available) {
      this.render();
      return;
    }
    try {
      const plans = await this.client.plan(
        this.provider,
        this.model,
        view.render.width,
        view.render.height,
      );
      if (token !== this.planToken) return;
      this.plans = plans;
    } catch (error) {
      if (token !== this.planToken) return;
      this.planError = message(error);
    }
    this.render();
  }

  private plan(): SizePlan | undefined {
    return this.plans.find((plan) => plan.target === this.target);
  }

  /** Cambiar lo que se enviaría invalida la revisión y crea una solicitud nueva. */
  private touched(): void {
    this.requestId = crypto.randomUUID();
    this.error = null;
    if (this.phase === 'review') this.phase = 'edit';
  }

  private problem(): string | null {
    if (this.infoError) return this.infoError;
    if (!this.info) return 'Consultando los proveedores de imágenes…';
    if (this.views.length === 0) return 'Guarda antes una vista (N) para usarla como origen.';
    if (this.selected.length === 0) return 'Elige al menos una vista de origen.';
    const provider = this.providerInfo();
    if (!provider) return 'No hay proveedores de imágenes en el servidor.';
    if (!provider.available) return `${provider.name}: ${provider.reason ?? 'no disponible'}`;
    if (!this.model.trim()) return 'Indica el modelo de generación.';
    if (!this.prompt.trim()) return 'Escribe el prompt: es la instrucción principal.';
    if (finalPrompt(this.prompt, this.keepGeometry).length > this.maxPrompt) {
      return `El prompt supera ${this.maxPrompt} caracteres.`;
    }
    if (this.planError) return this.planError;
    if (!this.plan()) return 'Consultando la resolución que admite el proveedor…';
    return null;
  }

  // --- Preparar las capturas de origen ---------------------------------------------------------

  /** Captura de cada vista elegida (la más reciente o una nueva en HD) y lo que se enviará. */
  private async prepareSources(): Promise<PreparedSource[]> {
    const { captures } = this.actions.ctx;
    const prepared: PreparedSource[] = [];
    for (const id of this.selected) {
      const view = this.views.find((v) => v.id === id);
      if (!view) continue;
      let capture = captures.library.latestCapture(id);
      let blob = capture ? await captures.library.blob(capture.id) : null;
      if (!capture || !blob) {
        capture = await captures.capture(id, 'HD'); // queda en la biblioteca como origen
        blob = await captures.blob(capture);
      }
      const reference = await referenceImage(blob, capture.width, capture.height);
      prepared.push({
        view,
        capture,
        sent: {
          data: await blobToBase64(reference.blob),
          width: reference.width,
          height: reference.height,
        },
      });
    }
    if (prepared.length === 0) throw new Error('Las vistas elegidas ya no existen.');
    return prepared;
  }

  private async review(): Promise<void> {
    this.error = null;
    try {
      this.sources = await this.prepareSources();
      this.phase = 'review';
    } catch (error) {
      this.error = message(error);
    }
    this.render();
  }

  private async suggest(): Promise<void> {
    if (this.suggesting) return;
    this.suggesting = true;
    this.error = null;
    this.render();
    try {
      const sources = await this.prepareSources();
      this.suggestion = await this.client.suggest(
        this.prompt,
        sources.map((source) => ({ data: source.sent.data })),
      );
    } catch (error) {
      this.error = message(error);
    } finally {
      this.suggesting = false;
      this.render();
    }
  }

  // --- Generar -----------------------------------------------------------------------------------

  private async generate(): Promise<void> {
    if (this.phase === 'running') return; // un segundo clic no lanza otra solicitud
    const provider = this.providerInfo();
    const first = this.sources[0];
    if (!provider || !first) return;
    const prompt = finalPrompt(this.prompt, this.keepGeometry);
    this.phase = 'running';
    this.error = null;
    this.job = null;
    this.abort = new AbortController();
    this.startedAt = Date.now();
    this.ticker = setInterval(() => this.renderProgress(), 1000);
    this.render();
    try {
      const { job, blob } = await this.client.generate(
        {
          provider: this.provider,
          model: this.model.trim(),
          prompt,
          target: this.target,
          images: this.sources.map((source) => ({ data: source.sent.data })),
          requestId: this.requestId,
        },
        {
          signal: this.abort.signal,
          onJob: (current) => {
            this.job = current;
            this.renderProgress();
          },
        },
      );
      this.result = await this.actions.ctx.captures.storeGenerated({
        view: first.view,
        blob,
        job,
        prompt,
        providerName: provider.name,
        sources: this.sources.map((source) => source.capture),
      });
      this.phase = 'done';
      this.requestId = crypto.randomUUID();
      this.actions.ctx.app.notify(
        `Imagen ${this.result.width}×${this.result.height} guardada en la biblioteca.`,
        'success',
      );
    } catch (error) {
      this.phase = 'review';
      this.error = message(error);
      // Respuesta definitiva del servidor (fallo, cancelación, rechazo): reintentar es una
      // solicitud nueva. Un corte de red conserva el identificador: no se duplica la llamada.
      if (!(error instanceof GenerationError) || error.definitive) {
        this.requestId = crypto.randomUUID();
      }
      if (!(error instanceof GenerationCancelled)) {
        this.actions.ctx.app.notify('La generación no se completó.', 'error');
      }
    } finally {
      if (this.ticker) clearInterval(this.ticker);
      this.ticker = null;
      this.abort = null;
      this.render();
    }
  }

  // --- Pintado ------------------------------------------------------------------------------------

  private render(): void {
    if (this.phase === 'running') this.renderRunning();
    else if (this.phase === 'done') this.renderDone();
    else if (this.phase === 'review') this.renderReview();
    else this.renderEdit();
  }

  private note(text: string, kind = ''): HTMLElement {
    return h('p', { class: `library-note${kind ? ` ${kind}` : ''}`, textContent: text });
  }

  private step(number: string, title: string, ...children: (Node | null)[]): HTMLElement {
    return h(
      'section',
      { class: 'generate-step' },
      h('h3', {}, h('span', { class: 'generate-num', textContent: number }), title),
      ...children,
    );
  }

  private renderEdit(): void {
    const max = this.maxReferences;
    const library = this.actions.ctx.captures.library;
    const viewList = h('div', { class: 'generate-views' });
    for (const view of this.views) {
      const checked = this.selected.includes(view.id);
      const capture = library.latestCapture(view.id);
      const box = h('input', { type: 'checkbox', checked });
      box.disabled = !checked && this.selected.length >= max;
      box.addEventListener('change', () => {
        this.selected = box.checked
          ? [...this.selected, view.id]
          : this.selected.filter((id) => id !== view.id);
        this.touched();
        this.render();
        void this.loadPlans();
      });
      const thumb = view.thumbnailDataUrl?.startsWith('data:image/')
        ? h('img', { src: view.thumbnailDataUrl, alt: '' })
        : icon('image', 14);
      viewList.append(
        h(
          'label',
          { class: `generate-view${checked ? ' is-checked' : ''}` },
          box,
          h('span', { class: 'generate-view-thumb' }, thumb),
          h(
            'span',
            { class: 'generate-view-text' },
            h('strong', { textContent: view.name }),
            h('span', {
              class: 'mono muted',
              textContent: capture
                ? `captura ${capture.width}×${capture.height}`
                : 'se capturará en HD al revisar',
            }),
          ),
        ),
      );
    }
    if (this.views.length === 0) {
      viewList.append(this.note('No hay vistas guardadas: encuadra el modelo y pulsa N.'));
    }

    const textarea = h('textarea', {
      class: 'input generate-prompt',
      rows: 5,
      value: this.prompt,
      placeholder:
        'Describe la imagen que quieres. Ej.: Conjunto residencial en ladera con fachadas de piedra clara, cubiertas ajardinadas, pinos y encinas, luz dorada de tarde.',
      spellcheck: true,
      attrs: { 'aria-label': 'Prompt' },
    });
    const counter = h('span', { class: 'ai-counter mono' });
    const syncCounter = (): void => {
      const length = finalPrompt(this.prompt, this.keepGeometry).length;
      counter.textContent = `${length}/${this.maxPrompt}`;
      counter.classList.toggle('is-over', length > this.maxPrompt);
    };
    textarea.addEventListener('input', () => {
      this.prompt = textarea.value;
      this.touched();
      syncCounter();
      this.renderFooter();
    });
    syncCounter();
    const chips = h('div', { class: 'generate-chips' });
    for (const suggestion of SUGGESTIONS) {
      chips.append(
        button({
          label: `+ ${suggestion}`,
          variant: 'ghost',
          size: 'sm',
          title: 'Añadir al prompt (opcional)',
          onClick: () => {
            const base = this.prompt.trim();
            this.prompt = base ? `${base.replace(/[,.;]$/, '')}, ${suggestion}` : suggestion;
            this.touched();
            this.render();
          },
        }),
      );
    }
    const keep = h('input', { type: 'checkbox', checked: this.keepGeometry });
    keep.addEventListener('change', () => {
      this.keepGeometry = keep.checked;
      this.touched();
      syncCounter();
    });
    const assist = this.info?.promptAssist;
    const assistButton = button({
      label: this.suggesting ? 'Claude está analizando las vistas…' : 'Mejorar con Claude',
      icon: 'palette',
      variant: 'secondary',
      size: 'sm',
      title: assist?.available
        ? `Envía las vistas elegidas y tu borrador a Anthropic (${assist.model}) para proponer un prompt. No genera la imagen.`
        : (assist?.reason ?? 'No disponible'),
      onClick: () => void this.suggest(),
    });
    assistButton.disabled = !assist?.available || this.suggesting || this.selected.length === 0;
    let suggestionBox: HTMLElement | null = null;
    if (this.suggestion !== null) {
      const edit = h('textarea', {
        class: 'input generate-prompt',
        rows: 5,
        value: this.suggestion,
        attrs: { 'aria-label': 'Sugerencia de Claude (editable)' },
      });
      edit.addEventListener('input', () => (this.suggestion = edit.value));
      suggestionBox = h(
        'div',
        { class: 'generate-suggestion' },
        h('strong', { textContent: 'Sugerencia de Claude (puedes editarla)' }),
        edit,
        h(
          'div',
          { class: 'mat-row-actions' },
          button({
            label: 'Usar esta sugerencia',
            variant: 'primary',
            size: 'sm',
            onClick: () => {
              this.prompt = (this.suggestion ?? '').trim();
              this.suggestion = null;
              this.touched();
              this.render();
            },
          }),
          button({
            label: 'Descartar',
            variant: 'ghost',
            size: 'sm',
            onClick: () => {
              this.suggestion = null;
              this.render();
            },
          }),
        ),
      );
    }

    const providerSelect = h('select', { class: 'select', attrs: { 'aria-label': 'Proveedor' } });
    for (const provider of this.info?.providers ?? []) {
      providerSelect.append(
        h('option', {
          value: provider.id,
          textContent: provider.available ? provider.name : `${provider.name} (no disponible)`,
        }),
      );
    }
    providerSelect.value = this.provider;
    providerSelect.addEventListener('change', () => {
      this.provider = providerSelect.value;
      this.model = this.providerInfo()?.models[0] ?? '';
      this.touched();
      this.render();
      void this.loadPlans();
    });
    const modelInput = h('input', {
      class: 'input',
      type: 'text',
      value: this.model,
      spellcheck: false,
      attrs: { list: 'generate-models', 'aria-label': 'Modelo', autocomplete: 'off' },
    });
    modelInput.addEventListener('change', () => {
      this.model = modelInput.value.trim();
      this.touched();
      void this.loadPlans();
    });
    const models = h('datalist', { id: 'generate-models' });
    for (const model of this.providerInfo()?.models ?? [])
      models.append(h('option', { value: model }));
    const provider = this.providerInfo();

    const targets = h('div', { class: 'generate-targets', attrs: { role: 'radiogroup' } });
    for (const target of ['standard', 'hd', '4k'] as const) {
      const plan = this.plans.find((p) => p.target === target);
      const radio = h('input', {
        type: 'radio',
        name: 'generate-target',
        checked: this.target === target,
      });
      radio.addEventListener('change', () => {
        this.target = target;
        this.touched();
        this.render();
      });
      targets.append(
        h(
          'label',
          { class: `generate-target${this.target === target ? ' is-checked' : ''}` },
          radio,
          h('strong', { textContent: TARGET_LABEL[target] }),
          h('span', {
            class: 'mono',
            textContent: plan ? `se pide ${plan.label}` : '…',
          }),
          plan && !plan.native
            ? h('span', { class: 'lib-badge is-warn', textContent: 'no nativo' })
            : null,
        ),
      );
    }
    const plan = this.plan();

    this.body.replaceChildren(
      this.step(
        '1',
        `Vistas de origen (hasta ${max})`,
        viewList,
        this.note('La primera vista elegida fija la proporción y agrupa el resultado.'),
      ),
      this.step(
        '2',
        'Prompt',
        h('div', { class: 'generate-prompt-wrap' }, textarea, counter),
        chips,
        h(
          'label',
          { class: 'generate-check' },
          keep,
          h('span', { textContent: `Añadir al final: «${FIDELITY_SENTENCE}»` }),
        ),
        h('div', { class: 'mat-row-actions' }, assistButton),
        suggestionBox,
      ),
      this.step(
        '3',
        'Proveedor, modelo y resolución',
        h(
          'div',
          { class: 'generate-row' },
          h(
            'label',
            { class: 'field field-stack' },
            h('span', { class: 'field-label', textContent: 'Proveedor' }),
            providerSelect,
          ),
          h(
            'label',
            { class: 'field field-stack' },
            h('span', { class: 'field-label', textContent: 'Modelo' }),
            modelInput,
            models,
          ),
        ),
        provider && !provider.available
          ? this.note(`${provider.name}: ${provider.reason ?? 'no disponible'}`, 'is-warning')
          : null,
        provider?.simulated
          ? this.note(
              'Proveedor de prueba: devuelve una imagen sintética sin IA, para comprobar el flujo sin coste.',
              'is-warning',
            )
          : null,
        targets,
        plan ? this.note(plan.note, plan.native ? '' : 'is-warning') : null,
        this.info ? this.note(this.info.fidelityNote) : null,
      ),
    );
    this.renderFooter();
  }

  private renderFooter(): void {
    if (this.phase !== 'edit') return;
    const problem = this.problem();
    const next = button({
      label: 'Revisar envío',
      icon: 'eye',
      variant: 'primary',
      onClick: () => void this.review(),
    });
    next.disabled = problem !== null;
    this.footer.replaceChildren(
      h('span', {
        class: `generate-status${this.error ? ' is-error' : ''}`,
        textContent: this.error ?? problem ?? 'Aún no se ha enviado nada.',
        attrs: { role: 'status' },
      }),
      next,
    );
  }

  private renderReview(): void {
    const provider = this.providerInfo();
    const plan = this.plan();
    const images = h('div', { class: 'generate-sent' });
    for (const source of this.sources) {
      images.append(
        h(
          'figure',
          {},
          h('img', { src: source.capture.thumbnail, alt: '' }),
          h('figcaption', {
            class: 'mono',
            textContent: `${source.view.name} · se envía a ${source.sent.width}×${source.sent.height}`,
          }),
        ),
      );
    }
    this.body.replaceChildren(
      this.step(
        '✓',
        `Se enviará a ${provider?.name ?? this.provider}`,
        h(
          'dl',
          { class: 'kv generate-kv' },
          h('dt', { textContent: 'Proveedor' }),
          h('dd', { textContent: provider?.name ?? this.provider }),
          h('dt', { textContent: 'Modelo' }),
          h('dd', { class: 'mono', textContent: this.model }),
          h('dt', { textContent: 'Resolución' }),
          h('dd', {
            class: 'mono',
            textContent: plan
              ? `${TARGET_LABEL[this.target]} · se pide ${plan.label}${plan.native ? '' : ' (no nativo)'}`
              : '—',
          }),
          h('dt', { textContent: 'Imágenes' }),
          h('dd', { textContent: `${this.sources.length} captura(s) del visor` }),
        ),
        images,
        h('strong', { class: 'generate-label', textContent: 'Prompt exacto' }),
        h('pre', {
          class: 'generate-final',
          textContent: finalPrompt(this.prompt, this.keepGeometry),
        }),
        plan ? this.note(plan.note, plan.native ? '' : 'is-warning') : null,
        this.note(
          provider?.simulated
            ? 'Proveedor de prueba: no sale nada del servidor de Extrude y no tiene coste.'
            : 'Las capturas y el prompt salen del servidor de Extrude hacia el proveedor. Una pulsación = una generación (puede tener coste); no hay reintentos automáticos.',
        ),
        this.info ? this.note(this.info.fidelityNote) : null,
      ),
    );
    this.footer.replaceChildren(
      h('span', {
        class: `generate-status${this.error ? ' is-error' : ''}`,
        textContent: this.error ?? 'Revisa lo que se envía y confirma.',
        attrs: { role: this.error ? 'alert' : 'status' },
      }),
      button({
        label: 'Volver a editar',
        variant: 'ghost',
        onClick: () => {
          this.phase = 'edit';
          this.render();
        },
      }),
      button({
        label: this.error ? 'Reintentar generación' : `Enviar y generar`,
        icon: 'check',
        variant: 'primary',
        onClick: () => void this.generate(),
      }),
    );
  }

  private renderRunning(): void {
    this.body.replaceChildren(
      h(
        'div',
        { class: 'generate-running', attrs: { role: 'status', 'aria-live': 'polite' } },
        h(
          'div',
          { class: 'layer-heights-progress is-indeterminate' },
          h('span', { class: 'generate-elapsed' }),
          h('div', { class: 'progress-track' }, h('div', { class: 'progress-bar' })),
        ),
        this.note(
          'Puedes cerrar esta ventana: la imagen se guardará en la biblioteca al terminar. Cancelar deja de esperar y descarta el resultado, pero el proveedor puede terminar y facturarla.',
        ),
      ),
    );
    this.footer.replaceChildren(
      h('span', { class: 'generate-status', textContent: '' }),
      button({
        label: 'Cancelar',
        variant: 'secondary',
        onClick: () => this.abort?.abort(),
      }),
    );
    this.renderProgress();
  }

  private renderProgress(): void {
    const label = this.body.querySelector('.generate-elapsed');
    if (!label) return;
    const seconds = Math.round((Date.now() - this.startedAt) / 1000);
    const state =
      this.job?.state === 'running'
        ? `Generando en ${this.providerInfo()?.name ?? this.provider}`
        : this.job
          ? 'En cola en el servidor'
          : 'Enviando capturas y prompt';
    label.textContent = `${state} · ${seconds} s`;
  }

  private renderDone(): void {
    const image = this.result;
    if (!image) return;
    const smaller = image.matchesRequest === false;
    this.body.replaceChildren(
      h(
        'div',
        { class: 'generate-result' },
        h('img', { src: image.thumbnail, alt: 'Resultado generado' }),
        h(
          'dl',
          { class: 'kv generate-kv' },
          h('dt', { textContent: 'Recibido' }),
          h('dd', { class: 'mono', textContent: `${image.width}×${image.height}` }),
          h('dt', { textContent: 'Pedido' }),
          h('dd', { class: 'mono', textContent: image.requested?.label ?? '—' }),
          h('dt', { textContent: 'Archivo' }),
          h('dd', { class: 'mono', textContent: image.filename }),
        ),
        smaller
          ? this.note(
              `El proveedor devolvió ${image.width}×${image.height}, menos de lo pedido. Puedes ampliarla desde la biblioteca: quedará marcada como ampliada, no como nativa.`,
              'is-warning',
            )
          : null,
        image.simulated
          ? this.note(
              'Imagen de prueba del proveedor simulado: no la generó ninguna IA.',
              'is-warning',
            )
          : null,
        this.info ? this.note(this.info.fidelityNote) : null,
      ),
    );
    this.footer.replaceChildren(
      h('span', { class: 'generate-status', textContent: 'Guardada junto a su captura original.' }),
      button({
        label: 'Comparar con el original',
        icon: 'copy',
        variant: 'secondary',
        onClick: () => this.actions.compare(image),
      }),
      button({
        label: 'Descargar',
        icon: 'download',
        variant: 'secondary',
        onClick: () => this.actions.download(image),
      }),
      button({
        label: 'Generar otra',
        variant: 'primary',
        onClick: () => {
          this.reset();
          this.render();
        },
      }),
    );
  }
}
