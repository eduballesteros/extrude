import { describe, expect, it } from 'vitest';
import { type LockManagerLike, ProjectLock } from './projectLock';

/** LockManager en memoria con la semántica de Web Locks que usa ProjectLock. */
function fakeLocks(): LockManagerLike & { held: Set<string> } {
  const held = new Set<string>();
  const queues = new Map<string, (() => void)[]>();
  const grant = (name: string, callback: LockGrantedCallback<unknown>): Promise<unknown> => {
    held.add(name);
    return Promise.resolve(callback({ name, mode: 'exclusive' })).finally(() => {
      held.delete(name);
      queues.get(name)?.shift()?.();
    });
  };
  const request = ((
    name: string,
    options: LockOptions,
    callback: LockGrantedCallback<unknown>,
  ): Promise<unknown> => {
    if (!held.has(name)) return grant(name, callback);
    if (options.ifAvailable) return Promise.resolve(callback(null));
    return new Promise((resolve, reject) => {
      const queue = queues.get(name) ?? [];
      const run = () => void grant(name, callback).then(resolve, reject);
      queue.push(run);
      queues.set(name, queue);
      options.signal?.addEventListener('abort', () => {
        const index = queue.indexOf(run);
        if (index >= 0) queue.splice(index, 1);
        reject(new DOMException('abortado', 'AbortError'));
      });
    });
  }) as LockManagerLike['request'];
  return { request, held };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('candado de proyecto entre ventanas', () => {
  it('la primera ventana guarda; la segunda no, y lo recibe al cerrarse la primera', async () => {
    const locks = fakeLocks();
    const first = new ProjectLock(locks);
    const second = new ProjectLock(locks);
    let handedOver = 0;

    expect(await first.claim('plano.glb', () => undefined)).toBe(true);
    expect(await second.claim('plano.glb', () => (handedOver += 1))).toBe(false);
    expect(handedOver).toBe(0);

    first.release();
    await tick();
    await tick();
    expect(handedOver).toBe(1);
    expect(locks.held.has('extrude-project:plano.glb')).toBe(true); // ahora la tiene la segunda
  });

  it('proyectos distintos no se bloquean entre sí', async () => {
    const locks = fakeLocks();
    expect(await new ProjectLock(locks).claim('a.glb', () => undefined)).toBe(true);
    expect(await new ProjectLock(locks).claim('b.glb', () => undefined)).toBe(true);
  });

  it('al abrir otro modelo deja de esperar el anterior y suelta el que tenía', async () => {
    const locks = fakeLocks();
    const first = new ProjectLock(locks);
    const second = new ProjectLock(locks);
    let handedOver = 0;
    await first.claim('a.glb', () => undefined);
    await second.claim('a.glb', () => (handedOver += 1));

    expect(await second.claim('b.glb', () => undefined)).toBe(true); // cambia de modelo
    first.release();
    await tick();
    await tick();
    expect(handedOver).toBe(0); // ya no le interesaba a.glb
    expect(locks.held.has('extrude-project:a.glb')).toBe(false);

    second.release();
    await tick();
    expect(locks.held.has('extrude-project:b.glb')).toBe(false);
  });

  it('sin Web Locks (navegador antiguo) guarda como siempre', async () => {
    expect(await new ProjectLock(undefined).claim('a.glb', () => undefined)).toBe(true);
  });
});
