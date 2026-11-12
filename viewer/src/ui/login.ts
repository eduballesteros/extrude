import { login } from '../auth/session';
import { h } from './dom';

/** Diálogo mínimo de inicio de sesión para un servidor compartido. */
export class LoginDialog {
  readonly element: HTMLElement;
  private readonly user = h('input', {
    class: 'input',
    type: 'text',
    attrs: { autocomplete: 'username', 'aria-label': 'Usuario' },
    placeholder: 'usuario',
  });
  private readonly password = h('input', {
    class: 'input',
    type: 'password',
    attrs: { autocomplete: 'current-password', 'aria-label': 'Contraseña' },
    placeholder: 'contraseña',
  });
  private readonly message = h('p', { class: 'login-error', attrs: { role: 'alert' } });
  private pending: Array<(user: string) => void> = [];

  constructor(private readonly server: string) {
    const submit = h('button', { type: 'submit', class: 'btn btn-primary', textContent: 'Entrar' });
    const form = h(
      'form',
      { class: 'login-card' },
      h('h2', { textContent: 'Servidor compartido' }),
      h('p', {
        class: 'login-hint',
        textContent: 'Este servidor de Extrude tiene cuentas. Entra para ver tus proyectos.',
      }),
      this.user,
      this.password,
      this.message,
      submit,
    );
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      void this.submit(submit);
    });
    this.element = h(
      'div',
      { class: 'login-overlay', attrs: { role: 'dialog', 'aria-modal': 'true' } },
      form,
    );
    this.element.hidden = true;
    document.body.append(this.element);
  }

  /** Muestra el diálogo y resuelve con el usuario cuando entra. */
  ask(): Promise<string> {
    this.element.hidden = false;
    this.message.textContent = '';
    queueMicrotask(() => this.user.focus());
    return new Promise((resolve) => this.pending.push(resolve));
  }

  private async submit(button: HTMLButtonElement): Promise<void> {
    button.disabled = true;
    this.message.textContent = '';
    try {
      const user = await login(this.server, this.user.value.trim(), this.password.value);
      this.password.value = '';
      this.element.hidden = true;
      const waiting = this.pending;
      this.pending = [];
      waiting.forEach((resolve) => resolve(user));
    } catch (error) {
      this.message.textContent = error instanceof Error ? error.message : String(error);
    } finally {
      button.disabled = false;
    }
  }
}
