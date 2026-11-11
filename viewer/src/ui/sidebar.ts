import { h, icon } from './dom';
import type { IconName } from './icons';

interface Tab {
  id: string;
  label: string;
  icon: IconName;
  panel: HTMLElement;
}

/** Panel lateral con pestañas. */
export class Sidebar {
  readonly element: HTMLElement;
  private readonly tabs = h('div', { class: 'tabs', attrs: { role: 'tablist' } });
  private readonly body = h('div', { class: 'sidebar-body' });
  private readonly buttons = new Map<string, HTMLButtonElement>();
  private readonly panels = new Map<string, HTMLElement>();

  constructor(side: 'left' | 'right', tabs: Tab[], title?: string) {
    this.element = h('aside', { class: `sidebar sidebar-${side}` });
    if (title) this.element.append(h('div', { class: 'sidebar-title', textContent: title }));
    if (tabs.length > 1) this.element.append(this.tabs);
    this.element.append(this.body);
    for (const tab of tabs) {
      const b = h(
        'button',
        { type: 'button', class: 'tab', attrs: { role: 'tab' } },
        icon(tab.icon, 15),
        h('span', { textContent: tab.label }),
      );
      b.addEventListener('click', () => this.select(tab.id));
      this.tabs.append(b);
      this.buttons.set(tab.id, b);
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
}
