import type { App } from '../app';
import { RENDER_PRESETS, type ViewManager } from '../views/viewManager';
import type { SceneView } from '../views/viewState';
import type { FrameOverlay } from './frameOverlay';

/** Tiempo para confirmar un borrado con un segundo clic (sin diálogos modales). */
const CONFIRM_MS = 3000;
const MAX_RENDER_PX = 16384;

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Partial<HTMLElementTagNameMap[K]> = {},
  ...children: (Node | string)[]
): HTMLElementTagNameMap[K] {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

function button(label: string, title: string, onClick: () => void): HTMLButtonElement {
  const b = el('button', { type: 'button', textContent: label, title });
  b.addEventListener('click', onClick);
  return b;
}

/** Panel lateral de vistas: crear, restaurar, renombrar, duplicar, reordenar, borrar y exportar. */
export class ViewsPanel {
  readonly element = el('section', { className: 'views-panel' });
  private readonly list = el('ol', { className: 'views-list' });
  private readonly widthInput = el('input', {
    type: 'number',
    min: '16',
    max: String(MAX_RENDER_PX),
  });
  private readonly heightInput = el('input', {
    type: 'number',
    min: '16',
    max: String(MAX_RENDER_PX),
  });
  private readonly frameToggle = el('input', { type: 'checkbox' });

  constructor(
    private readonly app: App,
    private readonly views: ViewManager,
    private readonly frame: FrameOverlay,
  ) {
    this.element.append(this.header(), this.renderSettings(), this.list, this.exports());
    document.body.appendChild(this.element);
    views.on(() => this.refresh());
    this.refresh();
  }

  private header(): HTMLElement {
    const title = el('h2', { textContent: 'Vistas' });
    const collapse = button('–', 'Plegar el panel', () => {
      this.element.classList.toggle('collapsed');
      collapse.textContent = this.element.classList.contains('collapsed') ? '+' : '–';
    });
    const add = button('＋ Nueva vista', 'Guardar cámara, sol, luces y capas actuales', () =>
      this.guard(() => this.views.create()),
    );
    return el('header', {}, title, add, collapse);
  }

  private renderSettings(): HTMLElement {
    const preset = el('select', { title: 'Resolución de render' });
    preset.append(el('option', { value: '', textContent: 'Personalizada' }));
    RENDER_PRESETS.forEach((p, i) =>
      preset.append(
        el('option', { value: String(i), textContent: `${p.label} (${p.width}×${p.height})` }),
      ),
    );
    preset.addEventListener('change', () => {
      const p = RENDER_PRESETS[Number(preset.value)];
      if (p) this.views.setRender(p.width, p.height);
    });
    const applySize = (): void => {
      const w = Number(this.widthInput.value);
      const h = Number(this.heightInput.value);
      if (w >= 16 && h >= 16 && w <= MAX_RENDER_PX && h <= MAX_RENDER_PX) {
        preset.value = '';
        this.views.setRender(w, h);
      }
    };
    this.widthInput.addEventListener('change', applySize);
    this.heightInput.addEventListener('change', applySize);
    this.frameToggle.addEventListener('change', () => {
      this.views.showFrame = this.frameToggle.checked;
      this.syncFrame();
    });
    return el(
      'div',
      { className: 'render-settings' },
      preset,
      el('label', {}, this.widthInput, ' × ', this.heightInput, ' px'),
      el('label', {}, this.frameToggle, ' Marco de cámara'),
    );
  }

  private exports(): HTMLElement {
    const importInput = el('input', {
      type: 'file',
      accept: '.json,application/json',
      hidden: true,
    });
    importInput.addEventListener('change', () => {
      const file = importInput.files?.[0];
      importInput.value = '';
      if (!file) return;
      this.views
        .importViewsJson(file)
        .then((n) => this.app.notify(`${n} vistas importadas.`))
        .catch((error: unknown) => this.app.notify(`views.json no válido: ${String(error)}`));
    });
    return el(
      'footer',
      {},
      el('h3', { textContent: 'Exportar' }),
      button(
        'views.json',
        'Todas las vistas, con origin_offset para volver a coordenadas reales',
        () => this.guard(() => this.views.exportViewsJson()),
      ),
      button('Importar…', 'Añadir vistas desde un views.json', () => importInput.click()),
      button(
        'blender_setup.py',
        'Script de Blender: importa el GLB y crea cámaras, sol y luces',
        () => this.guard(() => this.views.exportBlenderScript()),
      ),
      button(
        'GLB + cámaras y luces',
        'Cámaras de todas las vistas y luces de la activa (KHR_lights_punctual)',
        () =>
          this.guard(() => {
            const skipped = this.views.exportGlbWithLights();
            if (skipped.length) this.app.notify(`GLB exportado. Omitido: ${skipped.join('; ')}`);
          }),
      ),
      button('PNG', 'Render de la vista actual a la resolución elegida', () => {
        this.app.notify('Renderizando PNG…');
        this.views
          .exportPng()
          .then(() => this.app.notify('PNG exportado.'))
          .catch((error: unknown) =>
            this.app.notify(`No se pudo exportar el PNG: ${String(error)}`),
          );
      }),
      importInput,
    );
  }

  private guard(action: () => unknown): void {
    try {
      action();
    } catch (error) {
      console.error(error);
      this.app.notify(String(error instanceof Error ? error.message : error));
    }
  }

  private syncFrame(): void {
    this.frame.set(this.views.aspect, this.views.showFrame);
  }

  private refresh(): void {
    this.widthInput.value = String(this.views.render.width);
    this.heightInput.value = String(this.views.render.height);
    this.frameToggle.checked = this.views.showFrame;
    this.syncFrame();
    this.list.replaceChildren(...this.views.views.map((view, i) => this.item(view, i)));
    if (this.views.views.length === 0) {
      this.list.append(
        el('li', {
          className: 'empty',
          textContent: 'Sin vistas: encuadra y pulsa "Nueva vista".',
        }),
      );
    }
  }

  private item(view: SceneView, index: number): HTMLLIElement {
    const { views } = this;
    const thumb = el('img', { alt: '', width: 96 });
    if (view.thumbnailDataUrl?.startsWith('data:image/')) thumb.src = view.thumbnailDataUrl;
    thumb.addEventListener('click', () => views.restore(view.id));
    const name = el('input', { value: view.name, title: 'Renombrar' });
    name.addEventListener('change', () => views.rename(view.id, name.value));
    const meta = el('small', {
      textContent: `${view.render.width}×${view.render.height} · ${view.camera.type === 'perspective' ? 'persp.' : 'orto.'}`,
    });
    const remove = button('🗑', 'Borrar (pulsa dos veces)', () => {
      if (remove.dataset['armed']) {
        views.remove(view.id);
        return;
      }
      remove.dataset['armed'] = '1';
      remove.textContent = '¿Borrar?';
      setTimeout(() => {
        delete remove.dataset['armed'];
        remove.textContent = '🗑';
      }, CONFIRM_MS);
    });
    const actions = el(
      'div',
      { className: 'actions' },
      button('↺', 'Restaurar (anima la cámara)', () => views.restore(view.id)),
      button('⟳', 'Actualizar con el estado actual', () => this.guard(() => views.update(view.id))),
      button('⧉', 'Duplicar', () => views.duplicate(view.id)),
      button('↑', 'Subir', () => views.move(view.id, -1)),
      button('↓', 'Bajar', () => views.move(view.id, 1)),
      remove,
    );
    const li = el(
      'li',
      { className: view.id === views.activeId ? 'active' : '' },
      thumb,
      el('div', {}, name, meta, actions),
    );
    li.dataset['index'] = String(index);
    return li;
  }
}
