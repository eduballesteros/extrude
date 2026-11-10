import type { PickInfo } from '../scene/picking';

/** Panel con los metadatos del objeto seleccionado. Solo textContent: los datos vienen del DWG. */
export class InfoPanel {
  readonly element = document.createElement('aside');

  constructor() {
    this.element.className = 'info';
    this.element.hidden = true;
  }

  show(info: PickInfo): void {
    this.element.replaceChildren();
    const title = document.createElement('h3');
    title.textContent = info.layer ? `${info.layer} · ${info.kind ?? ''}` : 'Sin capa';
    const list = document.createElement('dl');
    for (const [key, value] of info.fields) {
      const dt = document.createElement('dt');
      dt.textContent = key;
      const dd = document.createElement('dd');
      dd.textContent = value;
      list.append(dt, dd);
    }
    this.element.append(title, list);
    this.element.hidden = false;
  }

  hide(): void {
    this.element.hidden = true;
  }
}
