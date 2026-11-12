import { describe, expect, it } from 'vitest';
import { defaultState, type Vec3 } from '../state';
import {
  createView,
  duplicateView,
  frameRect,
  hiddenLayers,
  moveView,
  parseViewsFile,
  renderFov,
  sceneToBlender,
  sceneToDwg,
  toViewsFile,
  viewerFov,
  viewToState,
  ViewsFileError,
  VIEWS_SCHEMA_VERSION,
} from './viewState';

const ORIGIN: Vec3 = [230032.5, 4140015, 0];
const MODEL = { file: 'model.glb', originOffset: ORIGIN, crs: 'EPSG:25830' };

function sampleView(name = 'Alzado sur') {
  const state = defaultState();
  state.camera.position = [10, 20, 30];
  state.camera.target = [1, 2, 3];
  state.layers = { EDIFICIOS: { visible: true }, VEGETACION: { visible: false } };
  state.lights.push({
    id: 'l1',
    type: 'spot',
    position: [0, 10, 0],
    target: [0, 0, 0],
    color: '#ffffff',
    intensity: 1000,
    angleDeg: 25,
  });
  return createView(
    state,
    name,
    { width: 3840, height: 2160 },
    undefined,
    new Date('2026-01-02T03:04:05Z'),
  );
}

describe('conversión de coordenadas', () => {
  it('escena → DWG con origen UTM y cota', () => {
    const p: Vec3 = [12.5, 7, -3.25];
    const dwg = sceneToDwg(p, ORIGIN);
    expect(dwg).toEqual([230045, 4140018.25, 7]);
  });

  it('el norte del dibujo (+Y DWG) es −Z en la escena y +Y en Blender', () => {
    const north: Vec3 = [0, 0, -10];
    expect(sceneToDwg(north, ORIGIN)).toEqual([ORIGIN[0], ORIGIN[1] + 10, 0]);
    expect(sceneToBlender(north)).toEqual([0, 10, 0]);
  });

  it('escena → Blender equivale a DWG recentrado (Z arriba)', () => {
    const p: Vec3 = [4, 9, -2];
    const dwg = sceneToDwg(p, ORIGIN);
    expect(sceneToBlender(p)).toEqual([dwg[0] - ORIGIN[0], dwg[1] - ORIGIN[1], dwg[2] - ORIGIN[2]]);
  });
});

describe('marco de cámara y FOV', () => {
  it('rectángulo centrado según la relación de aspecto', () => {
    expect(frameRect(1600, 900, 16 / 9)).toEqual({ x: 0, y: 0, width: 1600, height: 900 });
    const wide = frameRect(1600, 900, 21 / 9);
    expect(wide.width).toBe(1600);
    expect(wide.height).toBeCloseTo(1600 / (21 / 9));
    expect(wide.y).toBeCloseTo((900 - wide.height) / 2);
    const tall = frameRect(1600, 900, 4 / 5);
    expect(tall.height).toBe(900);
    expect(tall.x).toBeCloseTo((1600 - 720) / 2);
  });

  it('FOV del render y del visor son inversas', () => {
    for (const aspect of [16 / 9, 21 / 9, 1, 4 / 5]) {
      const fov = renderFov(50, 1.6, aspect);
      expect(viewerFov(fov, 1.6, aspect)).toBeCloseTo(50, 9);
    }
    expect(renderFov(50, 1.6, 1)).toBeCloseTo(50); // el marco usa toda la altura
    expect(renderFov(50, 1.6, 2.4)).toBeLessThan(50); // marco más ancho: menos alto
  });
});

describe('vistas', () => {
  it('createView copia el estado (sin alias) y etiqueta el aspecto', () => {
    const state = defaultState();
    const view = createView(state, 'A', { width: 1920, height: 1080 });
    state.sun.intensity = 99;
    expect(view.sun.intensity).not.toBe(99);
    expect(view.render.aspect).toBe('16:9');
    expect(viewToState(view).sun).toEqual(view.sun);
  });

  it('duplicar genera ids nuevos (también en las luces)', () => {
    const view = sampleView();
    const copy = duplicateView(view);
    expect(copy.id).not.toBe(view.id);
    expect(copy.name).toBe('Alzado sur (copia)');
    expect(copy.lights[0]!.id).not.toBe(view.lights[0]!.id);
  });

  it('reordenar respeta los extremos', () => {
    const [a, b, c] = [sampleView('a'), sampleView('b'), sampleView('c')];
    const views = [a, b, c];
    expect(moveView(views, b.id, -1).map((v) => v.name)).toEqual(['b', 'a', 'c']);
    expect(moveView(views, a.id, -1)).toBe(views);
    expect(moveView(views, c.id, 1)).toBe(views);
  });

  it('capas ocultas', () => {
    expect(hiddenLayers(sampleView())).toEqual(['VEGETACION']);
  });
});

describe('views.json', () => {
  it.each([
    ['render.width', 0],
    ['render.height', -1],
    ['render.width', 1.5],
    ['camera.fovDeg', 0],
    ['camera.fovDeg', 180],
    ['camera.orthoSize', 0],
    ['camera.near', -1],
    ['camera.far', 0.01],
    ['camera.up', [0, 0, 0]],
    ['camera.target', [10, 20, 30]],
    ['camera.position', [1e300, 0, 0]],
    ['sun.lat', 91],
    ['sun.isoDate', '2024-02-30T12:00:00Z'],
    ['sun.intensity', -1],
    ['sun.mode', 'wrong'],
    ['ambient.background', 'wrong'],
    ['ground.enabled', 'yes'],
    ['tonemapping.exposure', -1],
    ['shadows.mapSize', 3000],
    ['shadows.enabled', null],
    ['lights.0.intensity', -1],
    ['lights.0.angleDeg', 0],
    ['lights.0.target', [0, 10, 0]],
    ['layers.EDIFICIOS.visible', 1],
  ])('rechaza %s con error por campo', (field, value) => {
    const view = sampleView();
    const keys = field.split('.');
    let obj = view as unknown as Record<string, unknown>;
    for (const key of keys.slice(0, -1)) obj = obj[key] as Record<string, unknown>;
    obj[keys.at(-1)!] = value;
    const expected = field
      .replace('.0.', '[0].')
      .replace('layers.EDIFICIOS', 'layers["EDIFICIOS"]');
    expect(() => parseViewsFile(JSON.stringify({ schemaVersion: 1, views: [view] }))).toThrow(
      `views[0].${expected}`,
    );
  });

  it('permite clipping ortográfico negativo válido y rechaza near >= far', () => {
    const view = sampleView();
    view.camera.type = 'orthographic';
    view.camera.near = -5000;
    const parse = () => parseViewsFile(JSON.stringify({ schemaVersion: 1, views: [view] }));
    expect(parse().views[0]!.camera.near).toBe(-5000);
    view.camera.near = view.camera.far;
    expect(parse).toThrow('camera.far');
  });
  it('serializa con schemaVersion, origen y cámara en coordenadas reales', () => {
    const file = toViewsFile([sampleView()], MODEL, 'test');
    expect(file.schemaVersion).toBe(VIEWS_SCHEMA_VERSION);
    expect(file.coordinateSystem.originOffset).toEqual(ORIGIN);
    expect(file.coordinateSystem.crs).toBe('EPSG:25830');
    expect(file.views[0]!.cameraWorld.position).toEqual(sceneToDwg([10, 20, 30], ORIGIN));
  });

  it('ida y vuelta por JSON conserva la vista', () => {
    const view = sampleView();
    const text = JSON.stringify(toViewsFile([view], MODEL, 'test'));
    const parsed = parseViewsFile(text);
    expect(parsed.originOffset).toEqual(ORIGIN);
    expect(parsed.views).toEqual([view]);
  });

  it('rellena secciones ausentes con valores por defecto', () => {
    const view = sampleView() as unknown as Record<string, unknown>;
    delete view['ground'];
    delete view['shadows'];
    const parsed = parseViewsFile(JSON.stringify({ schemaVersion: 1, views: [view] }));
    expect(parsed.views[0]!.ground).toEqual(defaultState().ground);
    expect(parsed.views[0]!.shadows).toEqual(defaultState().shadows);
  });

  it('descarta miniaturas que no sean imágenes data:', () => {
    const view = { ...sampleView(), thumbnailDataUrl: 'javascript:alert(1)' };
    const parsed = parseViewsFile(JSON.stringify({ schemaVersion: 1, views: [view] }));
    expect(parsed.views[0]!.thumbnailDataUrl).toBeUndefined();
    const ok = { ...sampleView(), thumbnailDataUrl: 'data:image/jpeg;base64,AAAA' };
    expect(
      parseViewsFile(JSON.stringify({ schemaVersion: 1, views: [ok] })).views[0]!.thumbnailDataUrl,
    ).toBe('data:image/jpeg;base64,AAAA');
  });

  it.each([
    ['no es json', 'JSON válido'],
    [JSON.stringify({ schemaVersion: 99, views: [] }), 'schemaVersion'],
    [JSON.stringify({ schemaVersion: 1, views: [{ camera: {} }] }), 'camera.position'],
    [JSON.stringify({ schemaVersion: 1, views: {} }), 'lista'],
  ])('rechaza archivos inválidos con un mensaje claro (%#)', (text, fragment) => {
    expect(() => parseViewsFile(text)).toThrow(ViewsFileError);
    expect(() => parseViewsFile(text)).toThrow(fragment);
  });
});
