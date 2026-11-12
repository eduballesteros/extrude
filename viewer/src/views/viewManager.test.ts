import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { App } from '../app';
import { defaultState } from '../state';
import { loadViews, saveViews } from './storage';
import { ViewManager } from './viewManager';
import { createView, type SceneView, toViewsFile } from './viewState';

vi.mock('./storage', () => ({ loadViews: vi.fn(), saveViews: vi.fn() }));

const load = vi.mocked(loadViews);
const save = vi.mocked(saveViews);

/** Promesa que el test resuelve cuando quiere (orden de llegada del almacenamiento). */
function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

function view(name: string): SceneView {
  return createView(defaultState(), name, { width: 1920, height: 1080 });
}

/** App mínima: solo lo que usa ViewManager para cargar, guardar e importar. */
function fakeApp() {
  const listeners: (() => void)[] = [];
  const app = {
    model: null as { name: string; extras: null; tiles?: number } | null,
    on: (event: string, listener: () => void) => event === 'model' && listeners.push(listener),
    notify: vi.fn(),
  };
  const open = (name: string | null): void => {
    app.model = name ? { name, extras: null } : null;
    for (const listener of listeners) listener();
  };
  return { app: app as unknown as App, open, notify: app.notify };
}

const flush = (): Promise<void> => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  load.mockReset();
  save.mockReset().mockResolvedValue(undefined);
});

describe('ViewManager: cargas de vistas que llegan tarde', () => {
  it('rechaza la exportación Blender de teselas con una alternativa explícita', () => {
    const { app } = fakeApp();
    app.model = { name: 'barrio', extras: null, tiles: 2 } as App['model'];
    const views = new ViewManager(app);
    expect(() => views.exportBlenderScript()).toThrow(/teselados.*views.json/);
  });
  it('la carga de A que llega después de abrir B no sustituye las vistas de B', async () => {
    const a = deferred<SceneView[]>();
    const b = deferred<SceneView[]>();
    load.mockImplementation((key) => (key === 'A.glb' ? a.promise : b.promise));
    const { app, open } = fakeApp();
    const views = new ViewManager(app);

    open('A.glb');
    open('B.glb');
    b.resolve([view('Vista B')]);
    await flush();
    a.resolve([view('Vista A')]); // A termina la última
    await flush();

    expect(views.views.map((v) => v.name)).toEqual(['Vista B']);
    views.rename(views.views[0]!.id, 'Vista B renombrada');
    expect(save).toHaveBeenCalledTimes(1);
    const [key, saved] = save.mock.calls[0]!;
    expect(key).toBe('B.glb');
    expect(saved.map((v) => v.name)).toEqual(['Vista B renombrada']);
  });

  it('recargar el mismo modelo (materiales, edición, deshacer) no relee ni repone vistas', async () => {
    load.mockResolvedValueOnce([view('Vista A'), view('Atardecer')]);
    const { app, open } = fakeApp();
    const views = new ViewManager(app);
    open('A.glb');
    await flush();
    views.remove(views.views[1]!.id); // deshacer una vista creada por la IA
    open('A.glb'); // recarga del GLB editado o materializado
    await flush();
    expect(load).toHaveBeenCalledTimes(1);
    expect(views.views.map((v) => v.name)).toEqual(['Vista A']);
  });

  it('abrir dos veces el mismo modelo: solo cuenta la última carga', async () => {
    const first = deferred<SceneView[]>();
    const second = deferred<SceneView[]>();
    load.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const { app, open } = fakeApp();
    const views = new ViewManager(app);

    open('A.glb');
    open('A.glb');
    second.resolve([view('Actual')]);
    await flush();
    first.resolve([view('Obsoleta')]);
    await flush();
    expect(views.views.map((v) => v.name)).toEqual(['Actual']);
  });

  it('no guarda mientras carga; lo creado durante la carga se suma a lo guardado', async () => {
    const pending = deferred<SceneView[]>();
    load.mockReturnValue(pending.promise);
    const { app, open } = fakeApp();
    const views = new ViewManager(app);
    open('A.glb');

    const file = toViewsFile(
      [view('Importada')],
      { file: 'A.glb', originOffset: [0, 0, 0], crs: null },
      'test',
    );
    await views.importViewsJson(new File([JSON.stringify(file)], 'views.json'));
    expect(save).not.toHaveBeenCalled(); // habría pisado las vistas guardadas de A

    pending.resolve([view('Guardada 1'), view('Guardada 2')]);
    await flush();
    expect(views.views.map((v) => v.name)).toEqual(['Guardada 1', 'Guardada 2', 'Importada']);
    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0]![1]).toHaveLength(3);
  });

  it('si falla el almacenamiento de un modelo ya abandonado, no avisa ni toca el actual', async () => {
    const failing = deferred<SceneView[]>();
    load
      .mockReturnValueOnce(failing.promise.then(() => Promise.reject(new Error('IDB'))))
      .mockResolvedValueOnce([view('Vista B')]);
    const { app, open, notify } = fakeApp();
    const views = new ViewManager(app);
    open('A.glb');
    open('B.glb');
    await flush();
    failing.resolve([]);
    await flush();
    expect(views.views.map((v) => v.name)).toEqual(['Vista B']);
    expect(notify).not.toHaveBeenCalled();
  });
});
