import type { App } from '../app';
import { RENDER_PRESETS, type ViewManager } from '../views/viewManager';
import type { SceneView } from '../views/viewState';
import { button, h, icon, toggle } from './dom';
import type { FrameOverlay } from './frameOverlay';

/** Tiempo para confirmar un borrado con un segundo clic (sin diálogos modales). */
const CONFIRM_MS = 3000;
const MAX_RENDER_PX = 16384;

/** Pestaña Vistas: formato de render, marco de cámara y tarjetas de vistas guardadas. */
export class ViewsPanel {
  readonly element = h('div', { class: 'tab-panel' });
  private readonly list = h('ol', { class: 'view-list' });
  private readonly preset = h('select', { class: 'select', title: 'Formato de render' });
  private readonly width = h('input', {
    type: 'number',
    class: 'num',
    min: '16',
    max: String(MAX_RENDER_PX),
  });
  private readonly height = h('input', {
    type: 'number',
    class: 'num',
    min: '16',
    max: String(MAX_RENDER_PX),
  });
  private readonly frameToggle: ReturnType<typeof toggle>;

  constructor(
    private readonly app: App,
    private readonly views: ViewManager,
    private readonly frame: FrameOverlay,
  ) {
    this.preset.append(h('option', { value: '', textContent: 'Personalizado' }));
    RENDER_PRESETS.forEach((p, i) =>
      this.preset.append(
        h('option', { value: String(i), textContent: `${p.label} · ${p.width}×${p.height}` }),
      ),
    );
    this.preset.addEventListener('change', () => {
      const p = RENDER_PRESETS[Number(this.preset.value)];
      if (p) views.setRender(p.width, p.height);
    });
    const applySize = (): void => {
      const w = Number(this.width.value);
      const hh = Number(this.height.value);
      if (w >= 16 && hh >= 16 && w <= MAX_RENDER_PX && hh <= MAX_RENDER_PX) views.setRender(w, hh);
    };
    this.width.addEventListener('change', applySize);
    this.height.addEventListener('change', applySize);
    this.frameToggle = toggle('Marco de encuadre', views.showFrame, (v) => {
      views.showFrame = v;
      this.syncFrame();
    });

    const add = button({
      label: 'Nueva vista',
      icon: 'plus',
      variant: 'primary',
      title: 'Guardar cámara, sol, luces y capas (N)',
      onClick: () => this.guard(() => views.create()),
    });
    add.classList.add('btn-block');

    this.element.append(
      h(
        'div',
        { class: 'render-settings' },
        h('span', { class: 'field-label', textContent: 'Formato de render' }),
        this.preset,
        h(
          'div',
          { class: 'size-row' },
          this.width,
          h('span', { class: 'muted', textContent: '×' }),
          this.height,
          h('span', { class: 'muted', textContent: 'px' }),
        ),
        this.frameToggle.element,
      ),
      add,
      this.list,
    );
    views.on(() => this.render());
    this.render();
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
    this.width.value = String(width);
    this.height.value = String(height);
    const match = RENDER_PRESETS.findIndex((p) => p.width === width && p.height === height);
    this.preset.value = match >= 0 ? String(match) : '';
    this.frameToggle.set(views.showFrame);
    this.syncFrame();
    this.list.replaceChildren(...views.views.map((view, i) => this.card(view, i)));
    if (views.views.length === 0) {
      this.list.append(
        h(
          'li',
          { class: 'empty-state' },
          icon('views', 22),
          h('strong', { textContent: 'Sin vistas guardadas' }),
          h('span', { textContent: 'Encuadra la escena, ajusta el sol y pulsa "Nueva vista".' }),
        ),
      );
    }
  }

  private card(view: SceneView, index: number): HTMLLIElement {
    const { views } = this;
    const thumb = h('button', { type: 'button', class: 'view-thumb', title: 'Restaurar vista' });
    if (view.thumbnailDataUrl?.startsWith('data:image/')) {
      thumb.append(h('img', { src: view.thumbnailDataUrl, alt: '' }));
    } else {
      thumb.append(icon('image', 20));
    }
    thumb.append(h('span', { class: 'view-index', textContent: String(index + 1) }));
    thumb.addEventListener('click', () => views.restore(view.id));

    const name = h('input', { class: 'view-name', value: view.name, title: 'Renombrar' });
    name.addEventListener('change', () => views.rename(view.id, name.value));
    name.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') name.blur();
    });
    const time = view.sun.mode === 'datetime' ? view.sun.isoDate.slice(11, 16) : 'manual';
    const meta = h('span', {
      class: 'view-meta',
      textContent: `${view.render.width}×${view.render.height} · ${view.camera.type === 'perspective' ? 'Persp.' : 'Orto.'} · ☀ ${time}`,
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
      thumb,
      h('div', { class: 'view-body' }, name, meta, actions),
    );
  }
}
