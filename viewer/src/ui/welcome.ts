import type { LoadStage } from '../scene/loader';
import { button, h, icon } from './dom';

export interface SampleModel {
  title: string;
  description: string;
  url: string;
  /** Ilustración SVG constante (sin datos externos). */
  art: 'block' | 'city' | 'nested';
}

export const SAMPLE_MODELS: SampleModel[] = [
  {
    title: 'Manzana 2D',
    description: 'Edificios residenciales con patio y viales',
    url: '/models/block_2d.glb',
    art: 'block',
  },
  {
    title: 'Ciudad 50k',
    description: 'Escala metropolitana con 50.000 edificios y vegetación',
    url: '/models/city.glb',
    art: 'city',
  },
  {
    title: 'Bloques anidados',
    description: 'Instancias repetidas con rotaciones y atributos',
    url: '/models/nested.glb',
    art: 'nested',
  },
];

export const ACCEPTED = '.dwg,.dxf,.glb,.gltf,.yaml,.yml';

const STAGE_LABEL: Record<LoadStage | 'convert', string> = {
  convert: 'Convirtiendo el plano en el servidor…',
  download: 'Descargando modelo…',
  read: 'Leyendo archivo…',
  parse: 'Cargando geometría…',
  prepare: 'Aplicando sombras…',
};

/** Isométricas de trazo para las tarjetas (constantes). */
const ART: Record<SampleModel['art'], string> = {
  block:
    '<svg viewBox="0 0 120 72" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M20 44 46 31l26 13-26 13z"/><path d="M20 44v-12l26-13 26 13v12"/><path d="M46 19v12"/><path d="M33 38l13-6 13 6-13 6z" opacity=".5"/><path d="M74 50 92 41l18 9-18 9z"/><path d="M74 50v-8l18-9 18 9v8"/><path d="M8 62l52-26" opacity=".35"/></svg>',
  city: '<svg viewBox="0 0 120 72" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M14 52 30 44l16 8-16 8z"/><path d="M14 52V28l16-8 16 8v24"/><path d="M30 20v24"/><path d="M48 50 60 44l12 6-12 6z"/><path d="M48 50V18l12-6 12 6v32"/><path d="M60 12v32"/><path d="M76 54 90 47l14 7-14 7z"/><path d="M76 54V36l14-7 14 7v18"/><path d="M90 29v18"/></svg>',
  nested:
    '<svg viewBox="0 0 120 72" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><circle cx="24" cy="40" r="7"/><circle cx="42" cy="40" r="7"/><circle cx="60" cy="40" r="7"/><circle cx="33" cy="54" r="7"/><circle cx="51" cy="54" r="7"/><path d="M76 46 92 38l16 8-16 8z"/><path d="M76 46V30l16-8 16 8v16"/><path d="M92 22v16"/></svg>',
};

export interface WelcomeHandlers {
  onFile: (file: File) => void;
  onSample: (sample: SampleModel) => void;
  onClose: () => void;
}

/**
 * Pantalla de inicio (hub): visible sin modelo y al pulsar "Abrir" en la barra superior.
 * Muestra la zona de arrastre, ejemplos y el progreso de carga.
 */
export class WelcomeScreen {
  readonly element: HTMLElement;
  private readonly dropzone: HTMLElement;
  private readonly progress: HTMLElement;
  private readonly progressBar: HTMLElement;
  private readonly progressLabel: HTMLElement;
  private readonly progressValue: HTMLElement;
  private readonly error: HTMLElement;
  private readonly closeButton: HTMLButtonElement;
  private readonly configLabel: HTMLElement;
  private busy = false;

  constructor(handlers: WelcomeHandlers) {
    const input = h('input', { type: 'file', accept: ACCEPTED, hidden: true });
    input.addEventListener('change', () => {
      const file = input.files?.[0];
      input.value = '';
      if (file) handlers.onFile(file);
    });

    this.configLabel = h('span', { class: 'config-chip', hidden: true });
    this.dropzone = h(
      'div',
      {
        class: 'dropzone',
        tabIndex: 0,
        attrs: { role: 'button', 'aria-label': 'Arrastra o elige un archivo' },
      },
      h('div', { class: 'dropzone-icon' }, icon('upload', 28)),
      h('p', { class: 'dropzone-title', textContent: 'Arrastra aquí tu plano o modelo' }),
      h('p', {
        class: 'dropzone-hint',
        textContent:
          'DWG · DXF (se convierten con extrude serve) · GLB · GLTF — y opcionalmente su .yaml',
      }),
      button({
        label: 'Examinar equipo',
        icon: 'open',
        variant: 'primary',
        onClick: (e) => {
          e.stopPropagation();
          input.click();
        },
      }),
      this.configLabel,
      input,
    );
    this.dropzone.addEventListener('click', () => input.click());
    this.dropzone.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') input.click();
    });

    this.progressBar = h('div', { class: 'progress-fill' });
    this.progressLabel = h('span', { class: 'progress-label' });
    this.progressValue = h('span', { class: 'progress-value' });
    this.progress = h(
      'div',
      { class: 'progress', hidden: true },
      h('div', { class: 'progress-row' }, this.progressLabel, this.progressValue),
      h('div', { class: 'progress-track' }, this.progressBar),
    );
    this.error = h('div', { class: 'welcome-error', hidden: true, attrs: { role: 'alert' } });

    const gallery = h('div', { class: 'samples' });
    for (const sample of SAMPLE_MODELS) {
      const art = h('div', { class: 'sample-art' });
      art.innerHTML = ART[sample.art]; // constante del código
      const card = h(
        'button',
        { type: 'button', class: 'sample-card' },
        art,
        h('span', { class: 'sample-title', textContent: sample.title }),
        h('span', { class: 'sample-desc', textContent: sample.description }),
      );
      card.addEventListener('click', () => handlers.onSample(sample));
      gallery.append(card);
    }

    this.closeButton = button({
      icon: 'close',
      title: 'Volver al modelo (Esc)',
      variant: 'ghost',
      onClick: () => handlers.onClose(),
    });
    this.closeButton.classList.add('welcome-close');

    const brand = h(
      'div',
      { class: 'welcome-brand' },
      h('div', { class: 'brand-mark' }, icon('logo', 28)),
    );
    brand.append(
      h(
        'div',
        {},
        h('h1', { textContent: 'Extrude' }),
        h('p', { textContent: 'Conversor y visor urbano 3D con simulación solar' }),
      ),
    );

    this.element = h(
      'div',
      { class: 'welcome', attrs: { role: 'dialog', 'aria-label': 'Abrir modelo' } },
      this.closeButton,
      h(
        'div',
        { class: 'welcome-card' },
        brand,
        this.dropzone,
        this.progress,
        this.error,
        h('div', { class: 'samples-header' }, h('span', { textContent: 'Empieza con un ejemplo' })),
        gallery,
        h('p', {
          class: 'welcome-footnote',
          textContent:
            'Los ejemplos se cargan desde viewer/public/models (genéralos con just convert-samples).',
        }),
      ),
    );
    document.body.append(this.element);
  }

  get visible(): boolean {
    return !this.element.classList.contains('is-hidden');
  }

  /** Abre el hub. `canClose`: hay un modelo detrás al que volver. */
  open(canClose: boolean): void {
    this.closeButton.hidden = !canClose;
    this.clearError();
    this.element.classList.remove('is-hidden');
  }

  /** Desvanece el hub (transición al espacio de trabajo). */
  close(): void {
    this.element.classList.add('is-hidden');
  }

  setDragging(active: boolean): void {
    this.dropzone.classList.toggle('is-dragging', active);
  }

  setConfig(name: string | null): void {
    this.configLabel.hidden = !name;
    this.configLabel.textContent = name ? `Configuración: ${name}` : '';
  }

  get isBusy(): boolean {
    return this.busy;
  }

  startProgress(): void {
    this.busy = true;
    this.clearError();
    this.element.classList.add('is-busy');
    this.progress.hidden = false;
    this.setProgress('read', null);
  }

  setProgress(stage: LoadStage | 'convert', fraction: number | null): void {
    this.progressLabel.textContent = STAGE_LABEL[stage];
    const indeterminate = fraction === null;
    this.progress.classList.toggle('is-indeterminate', indeterminate);
    this.progressValue.textContent = indeterminate ? '' : `${Math.round(fraction * 100)} %`;
    this.progressBar.style.width = indeterminate ? '' : `${(fraction * 100).toFixed(1)}%`;
  }

  endProgress(): void {
    this.busy = false;
    this.element.classList.remove('is-busy');
    this.progress.hidden = true;
  }

  showError(message: string): void {
    this.error.replaceChildren(icon('alert', 16), h('span', { textContent: message }));
    this.error.hidden = false;
  }

  private clearError(): void {
    this.error.hidden = true;
  }
}
