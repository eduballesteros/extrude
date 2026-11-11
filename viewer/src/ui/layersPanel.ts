import type { App } from '../app';
import { LayerManager } from '../scene/layers';
import { button, compactNumber, formatNumber, h, icon } from './dom';

const KIND_LABEL: Record<string, string> = {
  building: 'Edificio',
  road: 'Vial',
  water: 'Agua',
  vegetation: 'Vegetación',
  terrain: 'Terreno',
  line: 'Líneas',
};

/** Pestaña Capas: lista densa con visibilidad, punto de color, triángulos (mono) y aislar. */
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

  constructor(private readonly app: App) {
    this.search.addEventListener('input', () => this.render());
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
        h('span', { textContent: 'Capa' }),
        h('span', { textContent: 'Tris' }),
      ),
      this.list,
      h('div', { class: 'panel-footer' }, this.summary),
    );
    app.on('model', () => this.render());
    app.on('state', () => this.render());
  }

  get count(): number {
    return this.app.layers.layers.size;
  }

  private apply(): void {
    this.app.applyLayers();
    this.render();
  }

  render(): void {
    const { app } = this;
    const query = this.search.value.trim().toLowerCase();
    const infos = [...app.layers.layers.values()];
    const shown = infos.filter((info) => info.name.toLowerCase().includes(query));
    this.list.replaceChildren(
      ...shown.map((info) => {
        const state = app.state.layers[info.name] ?? { visible: true };
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
        return h(
          'li',
          {
            class: `layer-row${state.visible ? '' : ' is-off'}`,
            title: `${info.name} · ${kind} · ${formatNumber(info.triangles)} triángulos`,
          },
          eye,
          color,
          h('span', { class: 'layer-name', textContent: info.name }),
          h('span', { class: 'layer-tris mono', textContent: compactNumber(info.triangles) }),
          isolate,
        );
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
  }
}
