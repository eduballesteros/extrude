import type { App } from '../app';
import { LayerManager } from '../scene/layers';
import { button, formatNumber, h } from './dom';

const KIND_LABEL: Record<string, string> = {
  building: 'Edificio',
  road: 'Vial',
  water: 'Agua',
  vegetation: 'Vegetación',
  terrain: 'Terreno',
  line: 'Líneas',
};

/** Pestaña Capas: visibilidad (ojo), color, aislar y recuento de triángulos. */
export class LayersPanel {
  readonly element = h('div', { class: 'tab-panel' });
  private readonly list = h('ul', { class: 'layer-list' });
  private readonly search = h('input', {
    type: 'search',
    class: 'search',
    placeholder: 'Buscar capa…',
  });
  private readonly summary = h('span', { class: 'muted' });

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
      this.list,
      h('div', { class: 'panel-footer' }, this.summary),
    );
    app.on('model', () => this.render());
    app.on('state', () => this.render());
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
        const eye = button({
          icon: state.visible ? 'eye' : 'eye-off',
          title: state.visible ? 'Ocultar' : 'Mostrar',
          variant: 'ghost',
          size: 'sm',
          onClick: () => {
            state.visible = !state.visible;
            this.apply();
          },
        });
        const original = `#${info.originalColors[0]?.getHexString() ?? 'cccccc'}`;
        const color = h('input', {
          type: 'color',
          class: 'swatch',
          value: state.colorOverride ?? original,
          title: 'Color de la capa',
        });
        color.addEventListener('input', () => {
          state.colorOverride = color.value;
          app.applyLayers();
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
        const reset = state.colorOverride
          ? button({
              icon: 'refresh',
              title: 'Color original',
              variant: 'ghost',
              size: 'sm',
              onClick: () => {
                delete state.colorOverride;
                this.apply();
              },
            })
          : null;
        return h(
          'li',
          { class: `layer-row${state.visible ? '' : ' is-hidden-layer'}` },
          eye,
          color,
          h(
            'div',
            { class: 'layer-text' },
            h('span', { class: 'layer-name', textContent: info.name, title: info.name }),
            h('span', {
              class: 'layer-meta',
              textContent: `${KIND_LABEL[info.kind] ?? info.kind} · ${formatNumber(info.triangles)} tri`,
            }),
          ),
          h('div', { class: 'row-actions' }, reset, isolate),
        );
      }),
    );
    if (shown.length === 0) {
      this.list.append(
        h('li', {
          class: 'empty',
          textContent: infos.length ? 'Ninguna capa coincide.' : 'Sin capas.',
        }),
      );
    }
    const visible = infos.filter((i) => app.state.layers[i.name]?.visible !== false).length;
    this.summary.textContent = `${visible} de ${infos.length} capas visibles`;
  }
}
