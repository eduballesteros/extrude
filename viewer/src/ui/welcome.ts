import type { LoadStage } from '../scene/loader';
import { ConversionDestination } from '../convert/destination';
import { button, h, icon } from './dom';

export interface SampleModel {
  title: string;
  /** Etiqueta técnica en monoespaciada (origen o característica del ejemplo). */
  tag: string;
  url: string;
}

export const SAMPLE_MODELS: SampleModel[] = [
  { title: 'Manzana 2D', tag: 'DXF', url: '/models/block_2d.glb' },
  { title: 'Manzana UTM', tag: 'UTM', url: '/models/block_2d_utm.glb' },
  { title: 'Bloques anidados', tag: 'Instancias', url: '/models/nested.glb' },
];

export const ACCEPTED = '.dwg,.dxf,.glb,.gltf,.yaml,.yml';
/** Formatos que se muestran en la zona para soltar (el .yaml es la configuración opcional). */
export const FORMAT_TAGS = ['.dwg', '.dxf', '.glb', '.gltf', '.yaml'];
export const APP_VERSION = (import.meta.env['VITE_APP_VERSION'] as string | undefined) ?? 'dev';

/** Atajos globales que se muestran en el pie (tecla, acción). */
export const SHORTCUTS: [string, string][] = [
  ['F', 'Encuadrar'],
  ['Espacio', 'Play sol'],
  ['1-9', 'Vistas'],
  ['N', 'Nueva vista'],
  ['M', 'Medir'],
  ['Tab', 'Paneles'],
];

/** Carga local (LoadStage), o conversión en extrude serve: 'convert' o 'convert:<fase>'. */
export type ProgressStage = LoadStage | 'convert' | `convert:${string}`;

const STAGE_LABEL: Record<string, string> = {
  convert: 'Convirtiendo plano (extrude serve)',
  'convert:upload': 'Enviando plano al servidor…',
  'convert:queued': 'En cola: hay otra conversión en curso…',
  'convert:draft_config': 'Generando configuración automática…',
  'convert:dwg_to_dxf': 'Convirtiendo DWG a DXF…',
  'convert:read': 'Leyendo entidades…',
  'convert:build': 'Construyendo geometría 3D…',
  'convert:instancing': 'Detectando bloques repetidos…',
  'convert:export': 'Exportando GLB…',
  'convert:compress': 'Comprimiendo GLB…',
  'convert:done': 'Conversión terminada',
  download: 'Descargando',
  read: 'Leyendo archivo',
  parse: 'Cargando geometría',
  prepare: 'Aplicando sombras',
};

/** Texto de la barra de progreso; una fase nueva del servidor cae en el genérico. */
export function stageLabel(stage: ProgressStage): string {
  return STAGE_LABEL[stage] ?? STAGE_LABEL['convert']!;
}

export interface WelcomeHandlers {
  onFile: (file: File) => void;
  onSample: (sample: SampleModel) => void;
  onClose: () => void;
  onCancel?: () => void;
  onRetry?: () => void;
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
  private readonly cancelButton: HTMLButtonElement;
  private readonly retryButton: HTMLButtonElement;
  private readonly destinationConsent: HTMLInputElement | null;
  private readonly recentButton: HTMLButtonElement;
  private onRecent: (() => void) | null = null;
  private readonly projects = h('div', { class: 'welcome-projects', hidden: true });
  private busy = false;

  constructor(handlers: WelcomeHandlers, destination = new ConversionDestination(null)) {
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
      h('div', { class: 'dropzone-icon' }, icon('upload', 22)),
      h('p', { class: 'dropzone-title', textContent: 'Suelta aquí tu plano o tu modelo 3D' }),
      h('p', {
        class: 'dropzone-hint',
        textContent: 'Abre modelos GLB/GLTF o convierte planos DWG/DXF con el destino indicado.',
      }),
      h(
        'div',
        { class: 'format-tags', attrs: { 'aria-label': 'Formatos admitidos' } },
        ...FORMAT_TAGS.map((tag) => h('span', { class: 'format-tag', textContent: tag })),
      ),
      button({
        label: 'Elegir archivo',
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

    const destinationInfo = h(
      'section',
      { class: 'conversion-destination', attrs: { 'aria-label': 'Destino de los planos' } },
      h('strong', { textContent: 'Destino de conversión' }),
      h('code', {
        textContent: destination.error ? 'Destino inválido · envío bloqueado' : destination.server,
      }),
      h('p', { textContent: destination.description }),
    );
    this.destinationConsent = null;
    if (destination.requiresAcceptance) {
      const consent = h('input', { type: 'checkbox', checked: false });
      consent.addEventListener('change', () => destination.setAccepted(consent.checked));
      this.destinationConsent = consent;
      destinationInfo.append(
        h('p', { textContent: 'Este destino remoto procede del enlace que has abierto.' }),
        h(
          'label',
          { class: 'conversion-consent' },
          consent,
          `Acepto enviar los planos y su configuración a ${destination.server}`,
        ),
      );
    }

    this.progressBar = h('div', { class: 'progress-fill' });
    this.progressLabel = h('span', { class: 'progress-label' });
    this.progressValue = h('span', { class: 'progress-value' });
    this.progress = h(
      'div',
      { class: 'progress', hidden: true },
      h('div', { class: 'progress-row' }, this.progressLabel, this.progressValue),
      h('div', { class: 'progress-track' }, this.progressBar),
    );
    this.cancelButton = button({
      label: 'Cancelar espera',
      variant: 'ghost',
      size: 'sm',
      title: 'Detener la espera del visor; el trabajo puede continuar en el servidor',
      onClick: () => handlers.onCancel?.(),
    });
    this.progress.append(this.cancelButton);
    this.cancelButton.hidden = true;
    this.error = h('div', { class: 'welcome-error', hidden: true, attrs: { role: 'alert' } });
    this.retryButton = button({ label: 'Reintentar', onClick: () => handlers.onRetry?.() });
    this.retryButton.hidden = true;

    this.recentButton = button({
      icon: 'refresh',
      variant: 'secondary',
      onClick: () => this.onRecent?.(),
    });
    this.recentButton.classList.add('welcome-recent');
    this.recentButton.hidden = true;
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
          h('span', { class: 'brand-mark' }, icon('logo', 28)),
          h(
            'div',
            { class: 'row' },
            h('h1', { class: 'welcome-title', textContent: 'Extrude' }),
            h('span', { class: 'version', textContent: `v${APP_VERSION}` }),
          ),
          h('p', {
            class: 'welcome-tagline',
            textContent:
              'De plano DWG/DXF a modelo 3D, con sol real y vistas listas para presentar.',
          }),
          this.closeButton,
        ),
        h(
          'div',
          { class: 'welcome-body' },
          h(
            'a',
            {
              class: 'welcome-guide',
              href: new URL('guia.html', document.baseURI).toString(),
              target: '_blank',
              rel: 'noopener',
              attrs: { 'aria-label': 'Guía de uso de Extrude (se abre en otra pestaña)' },
            },
            h(
              'span',
              { class: 'welcome-guide-icon', attrs: { 'aria-hidden': 'true' } },
              icon('book', 26),
            ),
            h(
              'span',
              { class: 'welcome-guide-content' },
              h('span', { class: 'welcome-guide-label', textContent: 'Primeros pasos' }),
              h('span', { class: 'welcome-guide-title', textContent: 'Guía de uso de Extrude' }),
              h('span', {
                class: 'welcome-guide-description',
                textContent: 'Prepara tu plano, edita el modelo y aprende a exportar tus vistas.',
              }),
              h(
                'span',
                { class: 'welcome-guide-action' },
                'Abrir la guía',
                h('span', { textContent: '↗', attrs: { 'aria-hidden': 'true' } }),
              ),
            ),
          ),
          this.dropzone,
          destinationInfo,
          this.progress,
          this.error,
          this.retryButton,
          this.recentButton,
          this.projects,
          h('div', { class: 'samples-label', textContent: 'O prueba con un ejemplo' }),
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

  /** Ofrece reabrir el último proyecto convertido (sigue en el servidor); null lo quita. */
  setRecent(name: string | null, onOpen?: () => void): void {
    this.recentButton.hidden = name === null;
    this.onRecent = onOpen ?? null;
    const label = `Reabrir el último proyecto: ${name ?? ''}`;
    this.recentButton.replaceChildren(icon('refresh'), h('span', { textContent: label }));
    this.recentButton.title =
      'Vuelve a cargar el proyecto desde el servidor, con su configuración, sus vistas y sus imágenes';
  }

  /**
   * Proyectos que conserva el servidor: se reabren sin volver a subir el plano. Los que tienen
   * objetos mal apoyados o son de una versión anterior se señalan para revisarlos.
   */
  setProjects<
    T extends {
      job: string;
      name: string;
      modified: string;
      floating: number;
      buried: number;
      outdated: boolean;
    },
  >(projects: T[], onOpen: (project: T) => void): void {
    this.projects.hidden = projects.length === 0;
    this.projects.replaceChildren(
      h('div', { class: 'samples-label', textContent: 'Proyectos en el servidor' }),
    );
    for (const project of projects.slice(0, 6)) {
      const issues = project.floating + project.buried;
      const flag = issues ? `${issues} mal apoyados` : project.outdated ? 'revisar' : '';
      const entry = h(
        'button',
        {
          type: 'button',
          class: 'welcome-project',
          title: 'Reabrir desde el servidor, con su configuración, sus vistas y sus imágenes',
        },
        h('span', { class: 'welcome-project-name', textContent: project.name }),
        h('span', {
          class: 'welcome-project-date mono',
          textContent: project.modified.replace('T', ' ').slice(0, 16),
        }),
        flag ? h('span', { class: 'lib-badge is-warn', textContent: flag }) : null,
      );
      entry.addEventListener('click', () => onOpen(project));
      this.projects.append(entry);
    }
  }

  setConfig(name: string | null): void {
    this.configLabel.hidden = !name;
    this.configLabel.textContent = name ? `config: ${name}` : '';
  }

  get isBusy(): boolean {
    return this.busy;
  }

  startProgress(cancelable = false): void {
    this.busy = true;
    if (this.destinationConsent) this.destinationConsent.disabled = true;
    this.clearError();
    this.element.classList.add('is-busy');
    this.progress.hidden = false;
    this.cancelButton.hidden = !cancelable;
    this.setProgress('read', null);
  }

  setProgress(stage: ProgressStage, fraction: number | null): void {
    this.progressLabel.textContent = stageLabel(stage);
    const indeterminate = fraction === null;
    this.progress.classList.toggle('is-indeterminate', indeterminate);
    this.progressValue.textContent = indeterminate ? '' : `${Math.round(fraction * 100)}%`;
    this.progressBar.style.width = indeterminate ? '' : `${(fraction * 100).toFixed(1)}%`;
  }

  endProgress(): void {
    this.busy = false;
    if (this.destinationConsent) this.destinationConsent.disabled = false;
    this.element.classList.remove('is-busy');
    this.progress.hidden = true;
  }

  showError(message: string): void {
    this.error.replaceChildren(icon('alert', 14), h('span', { textContent: message }));
    this.error.hidden = false;
  }

  setRetry(label: string | null): void {
    this.retryButton.hidden = label === null;
    this.retryButton.textContent = label ?? '';
  }

  private clearError(): void {
    this.error.hidden = true;
    this.retryButton.hidden = true;
  }
}
