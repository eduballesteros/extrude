import type { LoadStage } from '../scene/loader';
import { button, h, icon } from './dom';

export interface SampleModel {
  title: string;
  /** Etiqueta técnica en monoespaciada (origen o característica del ejemplo). */
  tag: string;
  url: string;
}

export const SAMPLE_MODELS: SampleModel[] = [
  { title: 'Manzana 2D', tag: 'DXF', url: '/models/block_2d.glb' },
  { title: 'Ciudad 50k', tag: 'UTM', url: '/models/city.glb' },
  { title: 'Bloques anidados', tag: 'Instancias', url: '/models/nested.glb' },
];

export const ACCEPTED = '.dwg,.dxf,.glb,.gltf,.yaml,.yml';
export const APP_VERSION = (import.meta.env['VITE_APP_VERSION'] as string | undefined) ?? 'dev';

/** Atajos globales que se muestran en el pie (tecla, acción). */
export const SHORTCUTS: [string, string][] = [
  ['F', 'Encuadrar'],
  ['Espacio', 'Play sol'],
  ['1-9', 'Vistas'],
  ['N', 'Nueva vista'],
  ['Tab', 'Paneles'],
];

const STAGE_LABEL: Record<LoadStage | 'convert', string> = {
  convert: 'Convirtiendo plano (extrude serve)',
  download: 'Descargando',
  read: 'Leyendo archivo',
  parse: 'Cargando geometría',
  prepare: 'Aplicando sombras',
};

export interface WelcomeHandlers {
  onFile: (file: File) => void;
  onSample: (sample: SampleModel) => void;
  onClose: () => void;
}

/**
 * Estado sin modelo: ventana compacta tipo "Nuevo proyecto" con zona de arrastre, ejemplos y
 * progreso de carga. También se abre desde la barra superior para cambiar de modelo.
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
      h('div', { class: 'dropzone-icon' }, icon('file', 20)),
      h('p', {
        class: 'dropzone-title',
        textContent: 'Arrastra un modelo .glb, .gltf o plano .dwg / .dxf',
      }),
      h('p', {
        class: 'dropzone-hint',
        textContent: 'Planos: conversión local con extrude serve · .yaml opcional',
      }),
      button({
        label: 'Examinar archivo local',
        variant: 'secondary',
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
      const card = h(
        'button',
        { type: 'button', class: 'sample-card', title: sample.url },
        h('span', { class: 'sample-title', textContent: sample.title }),
        h('span', { class: 'sample-tag', textContent: sample.tag }),
      );
      card.addEventListener('click', () => handlers.onSample(sample));
      gallery.append(card);
    }

    this.closeButton = button({
      icon: 'close',
      title: 'Volver al modelo (Esc)',
      variant: 'ghost',
      size: 'sm',
      onClick: () => handlers.onClose(),
    });
    this.closeButton.hidden = true; // sin modelo detrás no hay a dónde volver

    const shortcuts = h('div', { class: 'welcome-shortcuts' });
    for (const [key, action] of SHORTCUTS) {
      shortcuts.append(h('span', { class: 'shortcut' }, h('kbd', { textContent: key }), action));
    }

    this.element = h(
      'div',
      { class: 'welcome', attrs: { role: 'dialog', 'aria-label': 'Abrir modelo' } },
      h(
        'div',
        { class: 'welcome-card' },
        h(
          'header',
          { class: 'welcome-header' },
          h('span', { class: 'brand-mark' }, icon('logo', 16)),
          h('span', { class: 'welcome-title', textContent: 'Extrude' }),
          h('span', { class: 'version', textContent: `v${APP_VERSION}` }),
          h('span', { class: 'spacer' }),
          this.closeButton,
        ),
        h(
          'div',
          { class: 'welcome-body' },
          this.dropzone,
          this.progress,
          this.error,
          h('div', { class: 'samples-label', textContent: 'Ejemplos' }),
          gallery,
        ),
        h('footer', { class: 'welcome-footer' }, shortcuts),
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

  close(): void {
    this.element.classList.add('is-hidden');
  }

  setDragging(active: boolean): void {
    this.dropzone.classList.toggle('is-dragging', active);
  }

  setConfig(name: string | null): void {
    this.configLabel.hidden = !name;
    this.configLabel.textContent = name ? `config: ${name}` : '';
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
    this.progressValue.textContent = indeterminate ? '' : `${Math.round(fraction * 100)}%`;
    this.progressBar.style.width = indeterminate ? '' : `${(fraction * 100).toFixed(1)}%`;
  }

  endProgress(): void {
    this.busy = false;
    this.element.classList.remove('is-busy');
    this.progress.hidden = true;
  }

  showError(message: string): void {
    this.error.replaceChildren(icon('alert', 14), h('span', { textContent: message }));
    this.error.hidden = false;
  }

  private clearError(): void {
    this.error.hidden = true;
  }
}
