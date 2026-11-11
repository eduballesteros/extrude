import { NOTICE_EVENT, type Notice } from '../app';
import { h, icon } from './dom';
import type { IconName } from './icons';

const DURATION_MS: Record<Notice['kind'], number> = {
  info: 4000,
  success: 3500,
  warning: 7000,
  error: 9000,
};
const ICON: Record<Notice['kind'], IconName> = {
  info: 'info',
  success: 'check',
  warning: 'alert',
  error: 'alert',
};
const MAX_VISIBLE = 4;

/** Pila de avisos en la esquina inferior derecha (escucha el evento global de la App). */
export class Toasts {
  readonly element = h('div', {
    class: 'toasts',
    attrs: { role: 'status', 'aria-live': 'polite' },
  });

  constructor() {
    document.body.append(this.element);
    window.addEventListener(NOTICE_EVENT, (event) =>
      this.show((event as CustomEvent<Notice>).detail),
    );
  }

  show({ message, kind }: Notice): void {
    const close = h(
      'button',
      { type: 'button', class: 'toast-close', title: 'Cerrar' },
      icon('close', 14),
    );
    const toast = h(
      'div',
      { class: `toast toast-${kind}` },
      icon(ICON[kind], 16),
      h('span', { class: 'toast-message', textContent: message }),
      close,
    );
    const dismiss = (): void => {
      toast.classList.add('is-leaving');
      setTimeout(() => toast.remove(), 200);
    };
    close.addEventListener('click', dismiss);
    this.element.append(toast);
    while (this.element.children.length > MAX_VISIBLE) this.element.firstElementChild?.remove();
    setTimeout(dismiss, DURATION_MS[kind]);
  }
}
