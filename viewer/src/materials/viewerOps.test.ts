/**
 * Regresiones de la auditoría de la Fase 3 en el visor: H5 (cámara de una vista), H4 (efectos
 * del visor validados antes de confirmar), H8 (rehacer el sol) y H10 (cuerpos HTTP).
 */
import { Box3, Vector3 } from 'three';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { App } from '../app';
import { Navigation } from '../scene/navigation';
import { defaultState, type ViewerState } from '../state';
import { AiClient } from './ai';
import {
  prepareViewerOps,
  redoEffects,
  undoEffects,
  type ViewerEffects,
  type ViewsTarget,
} from './viewerOps';

/** Navegación real (presetCamera) sobre una cámara mínima: objetivo en el origen a 17,3 m. */
function navigation(): Navigation {
  const fake = {
    orbit: { target: new Vector3(0, 0, 0) },
    perspective: { fov: 50 },
    distance: Math.hypot(10, 10, 10),
    snapshot: () => ({ ...defaultState().camera, position: [10, 10, 10], target: [0, 0, 0] }),
  };
  Object.setPrototypeOf(fake, Navigation.prototype);
  return fake as unknown as Navigation;
}

function fakeApp(): { app: App; states: ViewerState[] } {
  let state = defaultState();
  const states: ViewerState[] = [];
  const app = {
    model: null,
    nav: navigation(),
    snapshot: () => structuredClone(state),
    setState: (next: ViewerState) => {
      state = structuredClone(next);
      states.push(state);
    },
  };
  return { app: app as unknown as App, states };
}

function views(): ViewsTarget & { ids: string[] } {
  const ids: string[] = [];
  return {
    ids,
    has: (id) => ids.includes(id),
    remove: (id) => void ids.splice(ids.indexOf(id), 1),
    insertPrepared: (id) => void (ids.includes(id) || ids.push(id)),
  };
}

const direction = (camera: { position: number[]; target: number[] }): number[] => {
  const v = new Vector3(...(camera.position as [number, number, number])).sub(
    new Vector3(...(camera.target as [number, number, number])),
  );
  return v
    .normalize()
    .toArray()
    .map((c) => Math.round(c * 1000) / 1000);
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('H5: la vista guarda exactamente el preset pedido', () => {
  it('planta sin objetos: cenital desde el primer momento, sin esperar animaciones', () => {
    const { app, states } = fakeApp();
    const prepared = prepareViewerOps(app, [
      {
        id: 'op1',
        tool: 'view.create',
        args: { name: 'Planta', preset: 'top', time: null },
        objectIds: [],
      },
    ]);
    expect(direction(prepared.views[0]!.state.camera)).toEqual([0, 1, 0]);
    expect(states).toEqual([]); // preparar no toca el visor
  });

  it('planta encuadrando objetos: cenital y centrada en su caja, no una 3/4 fija', () => {
    const nav = navigation();
    const box = new Box3(new Vector3(20, 0, 20), new Vector3(30, 12, 30));
    const camera = nav.presetCamera('top', box);
    expect(direction(camera)).toEqual([0, 1, 0]);
    expect(camera.target).toEqual([25, 6, 25]);
    expect(direction(nav.presetCamera('south', box))).toEqual([0, 0, 1]);
  });
});

describe('H4: los efectos del visor se validan antes de confirmar', () => {
  it('«atardecer» sin puesta de sol (latitud 90, 21 de junio) falla al preparar', () => {
    const { app } = fakeApp();
    app.snapshot = () => ({ ...defaultState(), sun: { ...defaultState().sun, lat: 90 } });
    expect(() =>
      prepareViewerOps(app, [
        {
          id: 'op1',
          tool: 'scene.set_sun',
          args: { time: 'sunset', date: '2024-06-21' },
          objectIds: [],
        },
      ]),
    ).toThrow(/puesta de sol/);
  });
});

describe('H8: deshacer y rehacer el sol usan el estado guardado', () => {
  it('rehacer un cambio de sol sin vistas vuelve a la hora confirmada', () => {
    const { app } = fakeApp();
    const list = views();
    const effects: ViewerEffects = prepareViewerOps(app, [
      { id: 'op1', tool: 'scene.set_sun', args: { time: '18:30', date: null }, objectIds: [] },
    ]);
    undoEffects(app, list, effects);
    expect(app.snapshot().sun.isoDate).toContain('T12:00');
    redoEffects(app, list, effects);
    expect(app.snapshot().sun.isoDate).toContain('T18:30');
  });

  it('deshacer quita las vistas creadas y rehacer las repone con el mismo id', () => {
    const { app } = fakeApp();
    const list = views();
    const effects = prepareViewerOps(app, [
      {
        id: 'op1',
        tool: 'view.create',
        args: { name: 'Sur', preset: 'south', time: null },
        objectIds: [],
      },
    ]);
    redoEffects(app, list, effects);
    expect(list.ids).toEqual([effects.views[0]!.id]);
    undoEffects(app, list, effects);
    expect(list.ids).toEqual([]);
  });
});

describe('H10: el cuerpo de la respuesta también tiene plazo y forma', () => {
  const job = 'a'.repeat(32);

  it('un cuerpo que no termina tras cabeceras 200 acaba en timeout, no en {}', async () => {
    vi.useFakeTimers();
    const body = new ReadableStream({ start: () => undefined }); // nunca envía ni cierra
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response(body, { status: 200 }))),
    );
    const call = new AiClient('http://127.0.0.1:8765').project(job);
    const assertion = expect(call).rejects.toMatchObject({ kind: 'timeout' });
    await vi.advanceTimersByTimeAsync(16_000);
    await assertion;
  });

  it('JSON inválido con 200 es un error, no una respuesta vacía', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response('{"revision": ', { status: 200 }))),
    );
    await expect(new AiClient('http://127.0.0.1:8765').project(job)).rejects.toMatchObject({
      kind: 'invalid_response',
    });
  });

  it('un sobre sin los campos esperados se rechaza antes de llegar a la interfaz', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response('{}', { status: 200 }))),
    );
    await expect(new AiClient('http://127.0.0.1:8765').project(job)).rejects.toMatchObject({
      kind: 'invalid_response',
    });
  });
});
