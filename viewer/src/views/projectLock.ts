/**
 * Un proyecto abierto en dos ventanas del mismo navegador: las vistas se guardan como una lista
 * completa por proyecto, así que la última ventana en guardar borraría lo que guardó la otra.
 * Con Web Locks (compartidos por todas las pestañas del mismo origen), la primera ventana que
 * abre el proyecto se queda su candado y es la única que guarda; las demás esperan en segundo
 * plano y lo reciben, avisando, cuando la primera lo cierra o se cierra.
 */
export type LockManagerLike = Pick<LockManager, 'request'>;

const PREFIX = 'extrude-project:';

export class ProjectLock {
  /** Cada reclamación invalida las anteriores (cambio de modelo, cierre). */
  private token = 0;
  private releaseHeld: (() => void) | null = null;
  private abortWait: AbortController | null = null;

  constructor(private readonly locks: LockManagerLike | undefined = globalThis.navigator?.locks) {}

  /**
   * Intenta quedarse el candado del proyecto. Devuelve true si esta ventana es la que guarda.
   * Si otra ventana lo tiene, devuelve false y sigue esperando: cuando lo recibe, llama a
   * `onAcquiredLater` (salvo que entretanto se haya abierto otro modelo o cerrado este).
   * Sin Web Locks (navegadores antiguos) no hay coordinación posible: se guarda como siempre.
   */
  claim(key: string, onAcquiredLater: () => void): Promise<boolean> {
    this.release();
    const locks = this.locks;
    if (!locks) return Promise.resolve(true);
    const token = this.token;
    const name = PREFIX + key;
    return new Promise<boolean>((resolve) => {
      locks
        .request(name, { ifAvailable: true }, (lock) => {
          if (token !== this.token) return undefined;
          if (lock) {
            resolve(true);
            return this.hold();
          }
          resolve(false);
          this.wait(locks, name, token, onAcquiredLater);
          return undefined;
        })
        .catch(() => resolve(true));
    });
  }

  /** Suelta el candado (o deja de esperarlo): otra ventana puede quedárselo. */
  release(): void {
    this.token += 1;
    this.releaseHeld?.();
    this.releaseHeld = null;
    this.abortWait?.abort();
    this.abortWait = null;
  }

  private hold(): Promise<void> {
    return new Promise<void>((resolve) => {
      this.releaseHeld = resolve;
    });
  }

  private wait(locks: LockManagerLike, name: string, token: number, onAcquired: () => void): void {
    const controller = new AbortController();
    this.abortWait = controller;
    locks
      .request(name, { signal: controller.signal }, () => {
        if (token !== this.token) return undefined;
        this.abortWait = null;
        onAcquired();
        return this.hold();
      })
      .catch(() => undefined); // abortado al cambiar de modelo o cerrar
  }
}
