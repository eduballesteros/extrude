import type { App } from '../app';
import type { ViewManager } from '../views/viewManager';
import type { SceneView } from '../views/viewState';
import { button, h, icon, segmented, toggle } from './dom';
import type { FrameOverlay } from './frameOverlay';
import { imageItem, type LibraryActions } from './imageLibrary';

/** Tiempo para confirmar un borrado con un segundo clic (sin diálogos modales). */
const CONFIRM_MS = 3000;
const MAX_RENDER_PX = 16384;

/** Formatos rápidos de render (la anchura/altura exactas siguen siendo editables). */
export const RENDER_FORMATS = [
  { value: '16:9', width: 3840, height: 2160, title: '3840×2160 (4K UHD)' },
  { value: '4:3', width: 2880, height: 2160, title: '2880×2160' },
  { value: 'A3', width: 4961, height: 3508, title: 'A3 apaisado a 300 ppp · 4961×3508' },
  { value: '1:1', width: 2048, height: 2048, title: '2048×2048' },
] as const;
type FormatId = (typeof RENDER_FORMATS)[number]['value'] | '';

/** Formato rápido que corresponde a un tamaño, o '' si es personalizado. */
export function formatOf(width: number, height: number): FormatId {
  return RENDER_FORMATS.find((f) => f.width === width && f.height === height)?.value ?? '';
}

/** Pestaña Vistas: formato de render, marco de cámara y lista compacta de vistas guardadas. */
export class ViewsPanel {
  readonly element = h('div', { class: 'tab-panel' });
  private readonly list = h('ol', { class: 'view-list' });
  private readonly width = h('input', { type: 'text', inputMode: 'numeric', class: 'input num' });
  private readonly height = h('input', { type: 'text', inputMode: 'numeric', class: 'input num' });
  private readonly format: ReturnType<typeof segmented<FormatId>>;
  private readonly frameToggle: ReturnType<typeof toggle>;

  constructor(
    private readonly app: App,
    private readonly views: ViewManager,
    private readonly frame: FrameOverlay,
    private readonly library: LibraryActions,
    private readonly openLibrary: (viewId?: string) => void,
  ) {
    this.format = segmented<FormatId>(
      RENDER_FORMATS.map((f) => ({ value: f.value, label: f.value, title: f.title })),
      formatOf(views.render.width, views.render.height),
      (id) => {
        const f = RENDER_FORMATS.find((x) => x.value === id);
        if (f) views.setRender(f.width, f.height);
      },
    );
    const applySize = (): void => {
      const w = Number(this.width.value.trim());
      const hh = Number(this.height.value.trim());
      const valid = (n: number): boolean => Number.isInteger(n) && n >= 16 && n <= MAX_RENDER_PX;
      this.width.classList.toggle('is-invalid', !valid(w));
      this.height.classList.toggle('is-invalid', !valid(hh));
      if (valid(w) && valid(hh)) views.setRender(w, hh);
    };
    for (const input of [this.width, this.height]) {
      input.addEventListener('change', applySize);
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') input.blur();
      });
    }
    this.frameToggle = toggle('Marco de encuadre', views.showFrame, (v) => {
      views.showFrame = v;
      this.syncFrame();
    });

    const add = button({
      label: 'Nueva vista',
      icon: 'plus',
      variant: 'secondary',
      size: 'sm',
      title: 'Guardar cámara, sol, luces y capas (N)',
      onClick: () => this.guard(() => views.create()),
    });

    // Primero lo que se usa a diario (las vistas); el formato de render, abajo
    this.element.append(
      h('div', { class: 'list-header' }, h('span', { textContent: 'Vistas guardadas' }), add),
      this.list,
      h(
        'div',
        { class: 'panel-group render-settings' },
        h('div', { class: 'group-title', textContent: 'Render' }),
        h(
          'div',
          { class: 'field' },
          h('span', { class: 'field-label', textContent: 'Formato' }),
          this.format.element,
        ),
        h(
          'div',
          { class: 'field' },
          h('span', { class: 'field-label', textContent: 'Tamaño' }),
          h(
            'div',
            { class: 'size-row' },
            this.width,
            h('span', { class: 'muted', textContent: '×' }),
            this.height,
            h('span', { class: 'unit', textContent: 'px' }),
          ),
        ),
        this.frameToggle.element,
      ),
    );
    views.on(() => this.render());
    // La misma biblioteca que abre el menú principal: una sola lista de imágenes
    library.ctx.captures.library.on(() => this.render());
    this.render();
  }

  get count(): number {
    return this.views.views.length;
  }

  private guard(action: () => unknown): void {
    try {
      action();
    } catch (error) {
      this.app.notify(String(error instanceof Error ? error.message : error), 'error');
    }
  }

  private syncFrame(): void {
    this.frame.set(this.views.aspect, this.views.showFrame);
  }

  private render(): void {
    const { views } = this;
    const { width, height } = views.render;
    if (document.activeElement !== this.width) this.width.value = String(width);
    if (document.activeElement !== this.height) this.height.value = String(height);
    this.width.classList.remove('is-invalid');
    this.height.classList.remove('is-invalid');
    this.format.set(formatOf(width, height));
    this.frameToggle.set(views.showFrame);
    this.syncFrame();
    this.list.replaceChildren(...views.views.map((view, i) => this.card(view, i)));
    if (views.views.length === 0) {
      this.list.append(
        h(
          'li',
          { class: 'empty' },
          h('span', { textContent: 'Sin vistas. Encuadra y pulsa ' }),
          h('kbd', { textContent: 'N' }),
        ),
      );
    }
  }

  /** Capturar en HD o 4K, generar con IA y las imágenes ya creadas de la vista. */
  private images(view: SceneView): HTMLElement {
    const { captures } = this.library.ctx;
    const images = captures.library.forView(view.id);
    const tools = h('div', { class: 'view-tools' });
    for (const preset of ['HD', '4K'] as const) {
      const size = captures.size(view, preset);
      tools.append(
        button({
          label: `${preset} ${size.width}×${size.height}`,
          icon: 'image',
          title: `Capturar la vista a ${size.width}×${size.height} (${preset}), guardarla en la biblioteca y descargarla`,
          variant: 'secondary',
          size: 'sm',
          onClick: () => void this.library.capture(view, preset),
        }),
      );
    }
    tools.append(
      button({
        label: 'IA',
        icon: 'palette',
        title: 'Generar una imagen con IA a partir de esta vista',
        variant: 'secondary',
        size: 'sm',
        onClick: () => this.library.ctx.generate(view.id),
      }),
    );
    const box = h('div', { class: 'view-extra' }, tools);
    if (images.length) {
      const list = h('div', { class: 'view-images' });
      for (const image of images) list.append(imageItem(image, this.library, true));
      box.append(
        list,
        button({
          label: `Ver en la biblioteca (${images.length})`,
          variant: 'ghost',
          size: 'sm',
          onClick: () => this.openLibrary(view.id),
        }),
      );
    }
    return box;
  }

  private card(view: SceneView, index: number): HTMLLIElement {
    const { views } = this;
    const thumb = h('button', {
      type: 'button',
      class: 'view-thumb',
      title: index < 9 ? `Restaurar vista (${index + 1})` : 'Restaurar vista',
      attrs: { style: `aspect-ratio:${view.render.width}/${view.render.height}` },
    });
    if (view.thumbnailDataUrl?.startsWith('data:image/')) {
      thumb.append(h('img', { src: view.thumbnailDataUrl, alt: '' }));
    } else {
      thumb.append(icon('image', 14));
    }
    thumb.addEventListener('click', () => views.restore(view.id));

    const name = h('input', {
      class: 'view-name',
      value: view.name,
      title: 'Renombrar',
      spellcheck: false,
    });
    name.addEventListener('change', () => views.rename(view.id, name.value));
    name.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') name.blur();
      if (e.key === 'Escape') {
        name.value = view.name;
        name.blur();
      }
    });
    const time = view.sun.mode === 'datetime' ? view.sun.isoDate.slice(11, 16) : 'manual';
    const meta = h('span', {
      class: 'view-meta mono',
      textContent: `${view.render.width}×${view.render.height} · ${view.camera.type === 'perspective' ? 'P' : 'O'} · ${time}`,
    });

    const remove = button({
      icon: 'trash',
      title: 'Borrar (pulsa dos veces)',
      variant: 'ghost',
      size: 'sm',
    });
    remove.addEventListener('click', () => {
      if (remove.dataset['armed']) {
        views.remove(view.id);
        return;
      }
      remove.dataset['armed'] = '1';
      remove.classList.add('is-armed');
      remove.title = 'Pulsa otra vez para borrar';
      setTimeout(() => {
        delete remove.dataset['armed'];
        remove.classList.remove('is-armed');
      }, CONFIRM_MS);
    });
    const actions = h(
      'div',
      { class: 'row-actions' },
      button({
        icon: 'refresh',
        title: 'Actualizar con el estado actual',
        variant: 'ghost',
        size: 'sm',
        onClick: () => this.guard(() => views.update(view.id)),
      }),
      button({
        icon: 'copy',
        title: 'Duplicar',
        variant: 'ghost',
        size: 'sm',
        onClick: () => views.duplicate(view.id),
      }),
      button({
        icon: 'arrow-up',
        title: 'Subir',
        variant: 'ghost',
        size: 'sm',
        onClick: () => views.move(view.id, -1),
      }),
      button({
        icon: 'arrow-down',
        title: 'Bajar',
        variant: 'ghost',
        size: 'sm',
        onClick: () => views.move(view.id, 1),
      }),
      remove,
    );
    return h(
      'li',
      { class: `view-card${view.id === views.activeId ? ' is-active' : ''}` },
      h('span', { class: 'view-index mono', textContent: String(index + 1).padStart(2, '0') }),
      thumb,
      h('div', { class: 'view-body' }, name, meta),
      actions,
      this.images(view),
    );
  }
}
