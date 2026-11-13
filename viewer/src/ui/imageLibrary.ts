/**
 * Interfaz de la biblioteca de vistas e imágenes. El panel de vistas y la biblioteca del menú
 * principal usan las mismas piezas (`imageItem`, `ImageViewer`) sobre la misma `ImageLibrary`:
 * no hay dos listas que puedan desincronizarse.
 */
import type { App } from '../app';
import type { Captures } from '../library/captures';
import {
  type CapturePreset,
  kindLabel,
  type LibraryImage,
  resolutionLabel,
} from '../library/images';
import type { ViewManager } from '../views/viewManager';
import type { SceneView } from '../views/viewState';
import { button, h, icon } from './dom';

const CONFIRM_MS = 3000;

export interface LibraryContext {
  app: App;
  views: ViewManager;
  captures: Captures;
  /** Abre el flujo de generación con IA, opcionalmente con una vista ya elegida. */
  generate(viewId?: string): void;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString('es-ES', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatBytes(bytes: number): string {
  return bytes >= 1_048_576
    ? `${(bytes / 1_048_576).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} kB`;
}

/** Etiquetas que explican qué es exactamente la imagen (nunca se presenta lo que no es). */
export function imageBadges(image: LibraryImage): { text: string; kind: string; title: string }[] {
  const badges: { text: string; kind: string; title: string }[] = [];
  const resolution = resolutionLabel(image.width, image.height);
  if (image.upscaledFrom) {
    badges.push({
      text: `Ampliada${resolution ? ` a ${resolution}` : ''}`,
      kind: 'warn',
      title:
        'Ampliación por interpolación de la imagen recibida: no es una generación nativa a esta resolución.',
    });
  } else if (resolution) {
    badges.push({
      text: image.kind === 'ai' && !image.simulated ? `${resolution} nativo` : resolution,
      kind: 'ok',
      title:
        image.kind === 'ai'
          ? 'Resolución real del archivo que devolvió el proveedor.'
          : 'Renderizada por el visor a esta resolución.',
    });
  }
  if (image.kind === 'ai' && image.matchesRequest === false && !image.upscaledFrom) {
    badges.push({
      text: 'Menor que la pedida',
      kind: 'warn',
      title: `Se pidió ${image.requested?.label ?? 'otra resolución'} y el proveedor devolvió ${image.width}×${image.height}.`,
    });
  }
  return badges;
}

/** Acciones sobre imágenes, compartidas por el panel de vistas y la biblioteca. */
export class LibraryActions {
  readonly viewer: ImageViewer;

  constructor(readonly ctx: LibraryContext) {
    this.viewer = new ImageViewer(this);
  }

  private notifyError(error: unknown): void {
    this.ctx.app.notify(message(error), 'error');
  }

  open(image: LibraryImage): void {
    void this.viewer.show(image);
  }

  compare(image: LibraryImage): void {
    const source = this.ctx.captures.library.sourceOf(image);
    if (!source) {
      this.ctx.app.notify('No queda ninguna captura original de esa vista para comparar.');
      return;
    }
    void this.viewer.show(image, source);
  }

  download(image: LibraryImage): void {
    this.ctx.captures.download(image).catch((error: unknown) => this.notifyError(error));
  }

  async upscale(image: LibraryImage): Promise<void> {
    try {
      const result = await this.ctx.captures.upscale(image);
      this.ctx.app.notify(
        `Ampliada a ${result.width}×${result.height} y guardada aparte (no es 4K nativo).`,
        'success',
      );
    } catch (error) {
      this.notifyError(error);
    }
  }

  remove(image: LibraryImage): void {
    this.ctx.captures.library.remove(image.id).catch((error: unknown) => this.notifyError(error));
  }

  /** Captura la vista, la guarda en la biblioteca y la descarga. */
  async capture(view: SceneView, preset: CapturePreset): Promise<LibraryImage | null> {
    const { width, height } = this.ctx.captures.size(view, preset);
    try {
      this.ctx.app.notify(`Capturando «${view.name}» a ${width}×${height}…`);
      const image = await this.ctx.captures.capture(view.id, preset);
      await this.ctx.captures.download(image);
      this.ctx.app.notify(
        `Captura ${image.width}×${image.height} guardada en la biblioteca y descargada.`,
        'success',
      );
      return image;
    } catch (error) {
      this.notifyError(error);
      return null;
    }
  }
}

/** Botón que pide un segundo clic para borrar (sin diálogos modales). */
export function confirmButton(title: string, onConfirm: () => void): HTMLButtonElement {
  const el = button({ icon: 'trash', title, variant: 'ghost', size: 'sm' });
  el.addEventListener('click', (event) => {
    event.stopPropagation();
    if (el.dataset['armed']) {
      onConfirm();
      return;
    }
    el.dataset['armed'] = '1';
    el.classList.add('is-armed');
    el.title = 'Pulsa otra vez para confirmar';
    setTimeout(() => {
      delete el.dataset['armed'];
      el.classList.remove('is-armed');
      el.title = title;
    }, CONFIRM_MS);
  });
  return el;
}

/**
 * Una imagen de la biblioteca. `compact` (panel de vistas): miniatura, tipo, dimensiones y
 * acciones. Completa (biblioteca): además fecha, vista de origen, prompt y proveedor.
 */
export function imageItem(
  image: LibraryImage,
  actions: LibraryActions,
  compact: boolean,
): HTMLElement {
  const thumb = h('button', {
    type: 'button',
    class: 'lib-thumb',
    title: 'Abrir a tamaño completo',
    attrs: { style: `aspect-ratio:${image.width}/${image.height}` },
  });
  if (image.thumbnail.startsWith('data:image/')) {
    thumb.append(h('img', { src: image.thumbnail, alt: '' }));
  } else {
    thumb.append(icon('image', 14));
  }
  thumb.addEventListener('click', () => actions.open(image));

  const badges = h('span', { class: 'lib-badges' });
  for (const badge of imageBadges(image)) {
    badges.append(
      h('span', {
        class: `lib-badge is-${badge.kind}`,
        textContent: badge.text,
        title: badge.title,
      }),
    );
  }
  const title = h(
    'div',
    { class: 'lib-title' },
    h('span', { class: `lib-kind is-${image.kind}`, textContent: kindLabel(image) }),
    badges,
  );
  const meta = h('div', {
    class: 'lib-meta mono',
    textContent: `${image.width}×${image.height} · ${formatDate(image.createdAt)}${compact ? '' : ` · ${formatBytes(image.bytes)}`}`,
    title: image.filename,
  });
  const body = h('div', { class: 'lib-body' }, title, meta);
  if (!compact) {
    body.append(
      h('div', { class: 'lib-name', textContent: image.filename, title: 'Nombre del archivo' }),
      h('div', { class: 'lib-origin', textContent: `Vista de origen: ${image.viewName}` }),
    );
    if (image.kind === 'ai') {
      body.append(
        h('div', {
          class: 'lib-origin',
          textContent: `${image.providerName ?? image.provider ?? 'Proveedor'} · ${image.model ?? '—'}${image.requested ? ` · pedido: ${image.requested.label}` : ''}`,
        }),
        h('p', { class: 'lib-prompt', textContent: image.prompt ?? '', title: 'Prompt' }),
      );
    }
  }

  const row = h('div', { class: 'row-actions lib-actions' });
  if (image.kind === 'ai') {
    row.append(
      button({
        icon: 'copy',
        title: 'Comparar con la captura original',
        variant: 'ghost',
        size: 'sm',
        onClick: () => actions.compare(image),
      }),
    );
    const size = actions.ctx.captures.upscaleSize(image);
    if (size && !image.upscaledFrom) {
      row.append(
        button({
          icon: 'frame',
          title: `Ampliar a ${size.width}×${size.height} (interpolación; se guarda aparte y se marca como ampliada)`,
          variant: 'ghost',
          size: 'sm',
          onClick: () => void actions.upscale(image),
        }),
      );
    }
  }
  row.append(
    button({
      icon: 'download',
      title: `Descargar ${image.filename}`,
      variant: 'ghost',
      size: 'sm',
      onClick: () => actions.download(image),
    }),
    confirmButton('Eliminar esta imagen (la vista no se borra)', () => actions.remove(image)),
  );
  return h(
    'div',
    { class: `lib-item${compact ? ' is-compact' : ''}`, data: { image: image.id } },
    thumb,
    body,
    row,
  );
}

/** Imagen a tamaño completo, o comparación original / resultado con una cortinilla. */
export class ImageViewer {
  readonly element: HTMLElement;
  private readonly stage = h('div', { class: 'viewer-stage' });
  private readonly caption = h('div', { class: 'viewer-caption' });
  private readonly bar = h('div', { class: 'viewer-bar' });
  private urls: string[] = [];
  private token = 0;

  constructor(private readonly actions: LibraryActions) {
    const close = button({
      label: 'Cerrar',
      icon: 'close',
      variant: 'ghost',
      size: 'sm',
      onClick: () => this.close(),
    });
    this.element = h(
      'div',
      {
        class: 'image-viewer',
        hidden: true,
        attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Imagen' },
      },
      h('div', { class: 'viewer-top' }, this.caption, this.bar, close),
      this.stage,
    );
    this.element.addEventListener('click', (event) => {
      if (event.target === this.element || event.target === this.stage) this.close();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !this.element.hidden) {
        // Solo se cierra lo de encima: la biblioteca o el diálogo de debajo siguen abiertos
        event.stopImmediatePropagation();
        this.close();
      }
    });
    document.body.append(this.element);
  }

  get visible(): boolean {
    return !this.element.hidden;
  }

  close(): void {
    this.token += 1;
    this.element.hidden = true;
    this.stage.replaceChildren();
    for (const url of this.urls) URL.revokeObjectURL(url);
    this.urls = [];
  }

  private async url(image: LibraryImage): Promise<string | null> {
    const blob = await this.actions.ctx.captures.library.blob(image.id);
    if (!blob) return null;
    const url = URL.createObjectURL(blob);
    this.urls.push(url);
    return url;
  }

  private missing(image: LibraryImage): HTMLElement {
    return h(
      'div',
      { class: 'viewer-missing' },
      icon('alert', 20),
      h('p', {
        textContent: `El archivo «${image.filename}» ya no está en este navegador (se vació su almacenamiento). Solo queda su ficha: quítala de la biblioteca.`,
      }),
      button({
        label: 'Quitar de la biblioteca',
        variant: 'secondary',
        size: 'sm',
        onClick: () => {
          this.actions.remove(image);
          this.close();
        },
      }),
    );
  }

  /** Muestra `image`; con `original`, la comparación deslizante entre ambas. */
  async show(image: LibraryImage, original?: LibraryImage): Promise<void> {
    this.close();
    const token = this.token;
    this.element.hidden = false;
    this.caption.replaceChildren(
      h('strong', {
        textContent: original
          ? `Comparar · ${image.viewName}`
          : `${kindLabel(image)} · ${image.viewName}`,
      }),
      h('span', {
        class: 'mono',
        textContent: original
          ? `original ${original.width}×${original.height} · resultado ${image.width}×${image.height}`
          : `${image.width}×${image.height} · ${formatDate(image.createdAt)}`,
      }),
    );
    this.bar.replaceChildren(
      ...(original
        ? [
            button({
              label: 'Descargar original',
              icon: 'download',
              variant: 'ghost',
              size: 'sm',
              onClick: () => this.actions.download(original),
            }),
          ]
        : image.kind === 'ai'
          ? [
              button({
                label: 'Comparar con el original',
                icon: 'copy',
                variant: 'ghost',
                size: 'sm',
                onClick: () => this.actions.compare(image),
              }),
            ]
          : []),
      button({
        label: original ? 'Descargar resultado' : 'Descargar',
        icon: 'download',
        variant: 'secondary',
        size: 'sm',
        onClick: () => this.actions.download(image),
      }),
    );
    this.stage.replaceChildren(h('p', { class: 'viewer-loading', textContent: 'Abriendo…' }));
    const [url, originalUrl] = await Promise.all([
      this.url(image),
      original ? this.url(original) : Promise.resolve(null),
    ]);
    if (token !== this.token) return;
    if (!url) {
      this.stage.replaceChildren(this.missing(image));
      return;
    }
    if (!original) {
      this.stage.replaceChildren(
        h('img', {
          class: 'viewer-image',
          src: url,
          alt: `${kindLabel(image)} de ${image.viewName}`,
        }),
      );
      return;
    }
    if (!originalUrl) {
      this.stage.replaceChildren(this.missing(original));
      return;
    }
    this.stage.replaceChildren(this.comparison(url, originalUrl, image));
  }

  private comparison(resultUrl: string, originalUrl: string, image: LibraryImage): HTMLElement {
    const range = h('input', {
      type: 'range',
      class: 'compare-range',
      min: '0',
      max: '100',
      value: '50',
      attrs: { 'aria-label': 'Posición de la cortinilla entre original y resultado' },
    });
    const top = h('div', { class: 'compare-top' }, h('img', { src: originalUrl, alt: 'Original' }));
    const frame = h(
      'div',
      { class: 'compare', attrs: { style: `aspect-ratio:${image.width}/${image.height}` } },
      h('img', { class: 'compare-base', src: resultUrl, alt: 'Resultado de IA' }),
      top,
      h('span', { class: 'compare-tag is-left', textContent: 'Captura original' }),
      h('span', { class: 'compare-tag is-right', textContent: kindLabel(image) }),
      range,
    );
    const sync = (): void => frame.style.setProperty('--split', `${range.value}%`);
    range.addEventListener('input', sync);
    sync();
    return frame;
  }
}

/** Biblioteca del proyecto (menú principal): todas las imágenes agrupadas por vista de origen. */
export class LibraryDialog {
  readonly element: HTMLElement;
  private readonly body = h('div', { class: 'library-body' });
  private readonly title = h('h2', { class: 'library-title' });
  private readonly summary = h('span', { class: 'library-summary mono' });
  private focusView: string | null = null;

  constructor(private readonly actions: LibraryActions) {
    const { ctx } = actions;
    const empty = confirmButton('Vaciar la biblioteca de este proyecto', () => {
      ctx.captures.library
        .removeAll()
        .then((n) => ctx.app.notify(`${n} imágenes eliminadas de la biblioteca.`, 'info'))
        .catch((error: unknown) => ctx.app.notify(message(error), 'error'));
    });
    this.element = h(
      'div',
      {
        class: 'library-overlay',
        hidden: true,
        attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Biblioteca de imágenes' },
      },
      h(
        'div',
        { class: 'library-card' },
        h(
          'header',
          { class: 'library-header' },
          h('div', { class: 'library-heading' }, this.title, this.summary),
          button({
            label: 'Generar con IA',
            icon: 'palette',
            variant: 'primary',
            size: 'sm',
            onClick: () => ctx.generate(),
          }),
          empty,
          button({
            label: 'Cerrar',
            icon: 'close',
            variant: 'ghost',
            size: 'sm',
            onClick: () => this.close(),
          }),
        ),
        this.body,
      ),
    );
    this.element.addEventListener('click', (event) => {
      if (event.target === this.element) this.close();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && this.visible && !actions.viewer.visible) {
        event.stopImmediatePropagation();
        this.close();
      }
    });
    document.body.append(this.element);
    const refresh = (): void => {
      if (this.visible) this.render();
    };
    ctx.captures.library.on(refresh);
    ctx.views.on(refresh);
  }

  get visible(): boolean {
    return !this.element.hidden;
  }

  open(viewId?: string): void {
    this.focusView = viewId ?? null;
    this.element.hidden = false;
    this.render();
    if (viewId) {
      this.body.querySelector(`[data-view="${CSS.escape(viewId)}"]`)?.scrollIntoView();
    }
  }

  close(): void {
    this.element.hidden = true;
  }

  private render(): void {
    const { ctx } = this.actions;
    const library = ctx.captures.library;
    const project = library.project ?? '';
    this.title.textContent = `Biblioteca de vistas e imágenes${project ? ` · ${project}` : ''}`;
    const captures = library.images.filter((image) => image.kind === 'capture').length;
    this.summary.textContent = `${ctx.views.views.length} vistas · ${captures} capturas · ${library.images.length - captures} generadas`;
    const groups = library.groups(ctx.views.views);
    this.body.replaceChildren();
    if (library.volatile) {
      this.body.append(
        h('p', {
          class: 'library-note is-warning',
          textContent:
            'El navegador no deja guardar archivos: estas imágenes se perderán al cerrar la página. Descárgalas.',
        }),
      );
    }
    if (groups.length === 0) {
      this.body.append(
        h('p', {
          class: 'library-note',
          textContent:
            'Aún no hay vistas. Encuadra el modelo y pulsa N (o «Nueva vista» en el panel Vistas); después podrás capturarla en HD o 4K y generar imágenes a partir de ella.',
        }),
      );
      return;
    }
    for (const group of groups) {
      const view = ctx.views.views.find((v) => v.id === group.viewId);
      const header = h(
        'div',
        { class: 'library-group-header' },
        h('strong', {
          textContent: group.orphan ? `${group.viewName} (vista eliminada)` : group.viewName,
        }),
        h('span', {
          class: 'mono muted',
          textContent: `${group.images.length} ${group.images.length === 1 ? 'imagen' : 'imágenes'}`,
        }),
      );
      if (view) {
        const actions = h('div', { class: 'library-group-actions' });
        actions.append(
          button({
            label: 'Recuperar vista',
            icon: 'camera',
            title: 'Llevar el visor a esta vista',
            variant: 'ghost',
            size: 'sm',
            onClick: () => {
              ctx.views.restore(view.id);
              this.close();
            },
          }),
          ...(['HD', '4K'] as const).map((preset) => {
            const size = ctx.captures.size(view, preset);
            return button({
              label: `Capturar ${preset} · ${size.width}×${size.height}`,
              icon: 'image',
              title: `Renderizar la vista a ${size.width}×${size.height}, guardarla y descargarla`,
              variant: 'secondary',
              size: 'sm',
              onClick: () => void this.actions.capture(view, preset),
            });
          }),
          button({
            label: 'Generar con IA',
            icon: 'palette',
            variant: 'secondary',
            size: 'sm',
            onClick: () => ctx.generate(view.id),
          }),
        );
        header.append(actions);
      }
      const list = h('div', { class: 'library-items' });
      for (const image of group.images) list.append(imageItem(image, this.actions, false));
      if (group.images.length === 0) {
        list.append(
          h('p', {
            class: 'library-note',
            textContent: 'Sin imágenes todavía: captura esta vista en HD o 4K.',
          }),
        );
      }
      this.body.append(
        h(
          'section',
          {
            class: `library-group${group.viewId === this.focusView ? ' is-focus' : ''}`,
            data: { view: group.viewId },
          },
          header,
          list,
        ),
      );
    }
  }
}
