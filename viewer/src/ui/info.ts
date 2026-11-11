import type { Vector3 } from 'three';
import type { PickInfo } from '../scene/picking';
import { button, h } from './dom';

/** Filas principales de la tarjeta: etiqueta visible ← campo de picking, con formato. */
const meters =
  (digits: number) =>
  (v: string): string =>
    Number.isFinite(Number(v)) ? `${Number(v).toFixed(digits)} m` : v;
const PRIMARY: [string, string, (v: string) => string][] = [
  ['Altura', 'Altura (m)', meters(1)],
  ['Plantas', 'Plantas', (v) => v],
  ['Cota', 'Cota base (m)', meters(2)],
  ['Handle', 'Handle', (v) => `#${v.replace(/^#/, '').toUpperCase()}`],
];
const SKIP = new Set(PRIMARY.map(([, key]) => key));

/** Pares (etiqueta, valor) de la tarjeta, sin vacíos. Exportado para test. */
export function primaryRows(info: PickInfo): [string, string][] {
  const values = new Map(info.fields);
  const rows: [string, string][] = [['Capa', info.layer ?? '—']];
  for (const [label, key, fmt] of PRIMARY) {
    const value = values.get(key);
    if (value === undefined || value === '—' || value === '') continue;
    rows.push([label, fmt(value)]);
  }
  return rows;
}

/**
 * Tarjeta flotante mínima del objeto seleccionado (esquina inferior derecha). Solo textContent:
 * los datos vienen del DWG.
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
    const list = h('dl', { class: 'kv' });
    for (const [label, value] of primaryRows(info)) {
      list.append(h('dt', { textContent: label }), h('dd', { textContent: value, title: value }));
    }
    const details = h('dl', { class: 'kv' });
    for (const [key, value] of info.fields) {
      if (SKIP.has(key)) continue;
      details.append(h('dt', { textContent: key }), h('dd', { textContent: value, title: value }));
    }
    const focus = button({
      label: 'Enfocar',
      variant: 'ghost',
      size: 'sm',
      onClick: () => this.point && this.onFocus(this.point),
    });
    const close = button({
      icon: 'close',
      title: 'Cerrar',
      variant: 'ghost',
      size: 'sm',
      onClick: () => this.hide(),
    });
    this.element.replaceChildren(
      h('header', {}, h('span', { textContent: 'Selección' }), focus, close),
      list,
      h('details', {}, h('summary', { textContent: 'Más datos' }), details),
    );
    this.element.hidden = false;
  }

  hide(): void {
    this.element.hidden = true;
  }
}
