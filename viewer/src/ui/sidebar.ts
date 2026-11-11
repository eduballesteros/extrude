import { h } from './dom';

interface Tab {
  id: string;
  label: string;
  panel: HTMLElement;
}

/** Panel lateral acoplado. Con varias pestañas, cabecera de pestañas tipo editor con contador. */
export class Sidebar {
  readonly element: HTMLElement;
  private readonly tabs = h('div', { class: 'tabs', attrs: { role: 'tablist' } });
  private readonly body = h('div', { class: 'sidebar-body' });
  private readonly buttons = new Map<string, HTMLButtonElement>();
  private readonly counts = new Map<string, HTMLElement>();
  private readonly panels = new Map<string, HTMLElement>();

  constructor(side: 'left' | 'right', tabs: Tab[], title?: string) {
    this.element = h('aside', { class: `sidebar sidebar-${side}` });
    if (title) this.element.append(h('div', { class: 'sidebar-title', textContent: title }));
    else this.element.append(this.tabs);
    this.element.append(this.body);
    for (const tab of tabs) {
      const count = h('span', { class: 'tab-count' });
      const b = h(
        'button',
        { type: 'button', class: 'tab', attrs: { role: 'tab' } },
        h('span', { textContent: tab.label }),
        count,
      );
      b.addEventListener('click', () => this.select(tab.id));
      this.tabs.append(b);
      this.buttons.set(tab.id, b);
      this.counts.set(tab.id, count);
      this.panels.set(tab.id, tab.panel);
      this.body.append(tab.panel);
    }
    if (tabs[0]) this.select(tabs[0].id);
    document.body.append(this.element);
  }

  select(id: string): void {
    for (const [key, b] of this.buttons) {
      b.classList.toggle('is-active', key === id);
      b.setAttribute('aria-selected', String(key === id));
    }
    for (const [key, panel] of this.panels) panel.hidden = key !== id;
  }

  setCount(id: string, n: number): void {
    const el = this.counts.get(id);
    if (el) el.textContent = String(n);
  }
}
