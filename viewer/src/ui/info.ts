import type { Vector3 } from 'three';
import type { PickInfo } from '../scene/picking';
import { button, h } from './dom';

const KIND_LABEL: Record<string, string> = {
  building: 'Edificio',
  road: 'Vial',
  water: 'Agua',
  vegetation: 'Vegetación',
  terrain: 'Terreno',
  line: 'Líneas',
};

/** Campos destacados (en este orden) y el resto como detalles. */
const HIGHLIGHT = ['Altura (m)', 'Plantas', 'Cota base (m)'];

/**
 * Tarjeta de inspección del objeto seleccionado. Solo textContent: los datos vienen del DWG.
 */
export class InfoPanel {
  readonly element = h('aside', {
    class: 'inspect-card',
    hidden: true,
    attrs: { 'aria-live': 'polite' },
  });
  private point: Vector3 | null = null;

  constructor(private readonly onFocus: (point: Vector3) => void) {}

  show(info: PickInfo): void {
    this.point = info.point.clone();
    const values = new Map(info.fields);
    const stat = (label: string, short: string): HTMLElement | null => {
      const value = values.get(label);
      return value && value !== '—'
        ? h(
            'div',
            { class: 'stat' },
            h('span', { class: 'stat-value', textContent: value }),
            h('span', { class: 'stat-label', textContent: short }),
          )
        : null;
    };
    const stats = [
      stat('Altura (m)', 'altura m'),
      stat('Plantas', 'plantas'),
      stat('Cota base (m)', 'cota m'),
    ].filter((s): s is HTMLElement => s !== null);
    const details = h('dl', { class: 'details' });
    for (const [key, value] of info.fields) {
      if (HIGHLIGHT.includes(key)) continue;
      details.append(h('dt', { textContent: key }), h('dd', { textContent: value }));
    }
    const close = button({
      icon: 'close',
      title: 'Cerrar',
      variant: 'ghost',
      size: 'sm',
      onClick: () => this.hide(),
    });
    const focus = button({
      label: 'Enfocar',
      icon: 'focus',
      variant: 'primary',
      size: 'sm',
      onClick: () => this.point && this.onFocus(this.point),
    });
    this.element.replaceChildren(
      h(
        'header',
        {},
        h('span', { class: 'kind-dot', data: { kind: info.kind ?? 'unknown' } }),
        h(
          'div',
          { class: 'inspect-title' },
          h('strong', { textContent: info.layer ?? 'Sin capa' }),
          h('span', { textContent: KIND_LABEL[info.kind ?? ''] ?? info.kind ?? '' }),
        ),
        close,
      ),
      stats.length ? h('div', { class: 'stats-row' }, ...stats) : '',
      h('details', { class: 'advanced' }, h('summary', { textContent: 'Detalles' }), details),
      h('footer', {}, focus),
    );
    this.element.hidden = false;
  }

  hide(): void {
    this.element.hidden = true;
  }
}
