/**
 * Vistas: instantáneas serializables del estado del visor (cámara, sol, luces, capas…).
 *
 * Convención: `camera.fovDeg` y `camera.orthoSize` de una vista son los del RENDER FINAL
 * (resolución `render`), es decir, lo que muestra el marco de cámara. El visor adapta su
 * propio FOV al restaurar para que el marco coincida con ese encuadre.
 */
import {
  type AmbientState,
  type CameraState,
  defaultState,
  type GroundState,
  type LayerState,
  type LightState,
  type ShadowState,
  type SunState,
  type ToneMappingState,
  type Vec3,
  type ViewerState,
} from '../state';

export const VIEWS_SCHEMA_VERSION = 1;
export const AXIS_TRANSFORM = '(x, y, z)_dwg -> (x, z, -y)_gltf';

export interface RenderSettings {
  width: number;
  height: number;
  /** Relación de aspecto legible, p. ej. "16:9". */
  aspect: string;
}

export interface SceneView {
  id: string;
  name: string;
  createdAt: string;
  camera: CameraState;
  render: RenderSettings;
  sun: SunState;
  ambient: AmbientState;
  shadows: ShadowState;
  lights: LightState[];
  layers: Record<string, LayerState>;
  ground: GroundState;
  tonemapping: ToneMappingState;
  thumbnailDataUrl?: string;
}

export interface ModelInfo {
  /** Nombre del GLB al que se refieren las coordenadas. */
  file: string;
  /** Desplazamiento del recentrado (metros, ejes DWG), de `scene.extras.origin_offset`. */
  originOffset: Vec3;
  crs: string | null;
}

export interface ViewsFile {
  schemaVersion: number;
  generator: string;
  coordinateSystem: {
    description: string;
    units: 'm';
    up: 'Y';
    axisTransform: string;
    originOffset: Vec3;
    crs: string | null;
  };
  model: { file: string };
  views: (SceneView & {
    /** Cámara en coordenadas reales del dibujo (DWG, Z-up, sin recentrar). */
    cameraWorld: { position: Vec3; target: Vec3 };
  })[];
}

// --- Coordenadas ------------------------------------------------------------

/** Escena (glTF Y-up recentrada) → coordenadas reales del DWG (Z-up). */
export function sceneToDwg(p: Vec3, origin: Vec3): Vec3 {
  return [p[0] + origin[0], -p[2] + origin[1], p[1] + origin[2]];
}

/** Escena (Y-up) → Blender (Z-up), igual que hace su importador glTF. */
export function sceneToBlender(p: Vec3): Vec3 {
  return [p[0], -p[2], p[1]];
}

// --- Marco de cámara --------------------------------------------------------

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Mayor rectángulo de relación `aspect` centrado en el visor. */
export function frameRect(viewportW: number, viewportH: number, aspect: number): Rect {
  const viewportAspect = viewportW / viewportH;
  const width = aspect >= viewportAspect ? viewportW : viewportH * aspect;
  const height = aspect >= viewportAspect ? viewportW / aspect : viewportH;
  return { x: (viewportW - width) / 2, y: (viewportH - height) / 2, width, height };
}

/** Fracción vertical del visor que ocupa el marco (1 si el marco usa toda la altura). */
export function frameHeightFraction(viewportAspect: number, renderAspect: number): number {
  return renderAspect >= viewportAspect ? viewportAspect / renderAspect : 1;
}

const DEG = Math.PI / 180;

/** FOV vertical del render a partir del FOV del visor y el marco. */
export function renderFov(
  viewerFovDeg: number,
  viewportAspect: number,
  renderAspect: number,
): number {
  const k = frameHeightFraction(viewportAspect, renderAspect);
  return (2 * Math.atan(Math.tan((viewerFovDeg * DEG) / 2) * k)) / DEG;
}

/** Inversa de `renderFov`: FOV del visor que hace que el marco muestre el render. */
export function viewerFov(
  renderFovDeg: number,
  viewportAspect: number,
  renderAspect: number,
): number {
  const k = frameHeightFraction(viewportAspect, renderAspect);
  return (2 * Math.atan(Math.tan((renderFovDeg * DEG) / 2) / k)) / DEG;
}

// --- Vistas -----------------------------------------------------------------

export function aspectLabel(width: number, height: number): string {
  const gcd = (a: number, b: number): number => (b === 0 ? a : gcd(b, a % b));
  const d = gcd(Math.round(width), Math.round(height)) || 1;
  return `${Math.round(width) / d}:${Math.round(height) / d}`;
}

/**
 * Crea una vista a partir del estado. `state.camera` ya debe venir convertida al encuadre
 * del render (ver `renderFov`).
 */
export function createView(
  state: ViewerState,
  name: string,
  render: { width: number; height: number },
  thumbnailDataUrl?: string,
  now: Date = new Date(),
): SceneView {
  const copy = structuredClone(state);
  return {
    id: crypto.randomUUID(),
    name,
    createdAt: now.toISOString(),
    camera: copy.camera,
    render: { ...render, aspect: aspectLabel(render.width, render.height) },
    sun: copy.sun,
    ambient: copy.ambient,
    shadows: copy.shadows,
    lights: copy.lights,
    layers: copy.layers,
    ground: copy.ground,
    tonemapping: copy.tonemapping,
    ...(thumbnailDataUrl ? { thumbnailDataUrl } : {}),
  };
}

/** Estado del visor a partir de una vista (la cámara sigue en el encuadre del render). */
export function viewToState(view: SceneView): ViewerState {
  const copy = structuredClone(view);
  return {
    camera: copy.camera,
    sun: copy.sun,
    ambient: copy.ambient,
    shadows: copy.shadows,
    lights: copy.lights,
    layers: copy.layers,
    ground: copy.ground,
    tonemapping: copy.tonemapping,
  };
}

export function duplicateView(view: SceneView, now: Date = new Date()): SceneView {
  return {
    ...structuredClone(view),
    id: crypto.randomUUID(),
    name: `${view.name} (copia)`,
    createdAt: now.toISOString(),
    lights: view.lights.map((l) => ({ ...structuredClone(l), id: crypto.randomUUID() })),
  };
}

export function moveView(views: SceneView[], id: string, delta: -1 | 1): SceneView[] {
  const index = views.findIndex((v) => v.id === id);
  const target = index + delta;
  if (index < 0 || target < 0 || target >= views.length) return views;
  const copy = [...views];
  [copy[index], copy[target]] = [copy[target]!, copy[index]!];
  return copy;
}

/** Nombres de capa ocultos en una vista (para Blender). */
export function hiddenLayers(view: SceneView): string[] {
  return Object.entries(view.layers)
    .filter(([, s]) => !s.visible)
    .map(([name]) => name)
    .sort();
}

// --- views.json ---------------------------------------------------------------

export function toViewsFile(views: SceneView[], model: ModelInfo, generator: string): ViewsFile {
  return {
    schemaVersion: VIEWS_SCHEMA_VERSION,
    generator,
    coordinateSystem: {
      description:
        'Coordenadas de la escena exportada: metros, Y arriba, recentradas. Reales (DWG): ' +
        'x = x + o[0], y = -z + o[1], z = y + o[2] con o = originOffset.',
      units: 'm',
      up: 'Y',
      axisTransform: AXIS_TRANSFORM,
      originOffset: model.originOffset,
      crs: model.crs,
    },
    model: { file: model.file },
    views: views.map((view) => ({
      ...structuredClone(view),
      cameraWorld: {
        position: sceneToDwg(view.camera.position, model.originOffset),
        target: sceneToDwg(view.camera.target, model.originOffset),
      },
    })),
  };
}

export class ViewsFileError extends Error {}

const DATA_IMAGE = /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/;

function isVec3(v: unknown): v is Vec3 {
  return (
    Array.isArray(v) &&
    v.length === 3 &&
    v.every((n) => typeof n === 'number' && Number.isFinite(n))
  );
}

function requireObject(value: unknown, path: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new ViewsFileError(`${path}: se esperaba un objeto`);
  }
  return value as Record<string, unknown>;
}

function requireNumber(obj: Record<string, unknown>, key: string, path: string): number {
  const value = obj[key];
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new ViewsFileError(`${path}.${key}: se esperaba un número`);
  }
  return value;
}

/** Valida también secciones rellenadas desde defaults: ningún valor importado los elude. */
function validateView(view: SceneView, path: string): void {
  const fail = (field: string, message: string): never => {
    throw new ViewsFileError(`${path}.${field}: ${message}`);
  };
  const number = (field: string, value: number, min: number, max = 1e12): void => {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max)
      fail(field, `se esperaba un número entre ${min} y ${max}`);
  };
  const positive = (field: string, value: number): void => {
    number(field, value, 0);
    if (value < 1e-9) fail(field, 'debe ser positivo y al menos 1e-9');
  };
  const choice = (field: string, value: string, options: readonly string[]): void => {
    if (!options.includes(value)) fail(field, `debe ser ${options.join(', ')}`);
  };
  const bool = (field: string, value: boolean): void => {
    if (typeof value !== 'boolean') fail(field, 'se esperaba un booleano');
  };
  const color = (field: string, value: string): void => {
    if (typeof value !== 'string' || !/^#(?:[\da-f]{3}|[\da-f]{6})$/i.test(value))
      fail(field, 'color hexadecimal no válido');
  };
  const vector = (field: string, value: Vec3): void => {
    if (!isVec3(value) || value.some((n) => Math.abs(n) > 1e12))
      fail(field, 'se esperaba un vector finito [x, y, z] (magnitud por componente ≤ 1e12)');
  };
  const date = (field: string, value: string): void => {
    if (
      typeof value !== 'string' ||
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/.test(value) ||
      !Number.isFinite(Date.parse(value)) ||
      new Date(`${value.slice(0, 10)}T00:00:00Z`).toISOString().slice(0, 10) !== value.slice(0, 10)
    )
      fail(field, 'fecha ISO con zona horaria no válida');
  };
  for (const key of ['width', 'height'] as const) {
    const value = view.render[key];
    if (!Number.isInteger(value) || value < 1 || value > 16384)
      fail(`render.${key}`, 'debe ser un entero entre 1 y 16384');
  }
  const c = view.camera;
  choice('camera.type', c.type, ['perspective', 'orthographic']);
  vector('camera.position', c.position);
  vector('camera.target', c.target);
  vector('camera.up', c.up);
  const direction = c.target.map((v, i) => v - c.position[i]!);
  const length = Math.hypot(...direction);
  if (length < 1e-9) fail('camera.target', 'debe ser distinto de position');
  const upLength = Math.hypot(...c.up);
  if (upLength < 1e-9) fail('camera.up', 'no puede ser nulo');
  const dot = direction.reduce((sum, v, i) => sum + ((v / length) * c.up[i]!) / upLength, 0);
  if (Math.abs(dot) > 1 - 1e-10)
    fail('camera.up', 'no puede ser paralelo a la dirección de cámara');
  number('camera.fovDeg', c.fovDeg, 0.001, 179.999);
  if (c.fovDeg <= 0 || c.fovDeg >= 180)
    fail('camera.fovDeg', 'debe estar entre 0 y 180, excluidos');
  positive('camera.orthoSize', c.orthoSize);
  number('camera.near', c.near, c.type === 'orthographic' ? -1e12 : 0);
  if (c.type === 'perspective' && c.near < 1e-9)
    fail('camera.near', 'debe ser positivo en perspectiva');
  positive('camera.far', c.far);
  if (c.far <= c.near) fail('camera.far', 'debe superar near');
  const s = view.sun;
  choice('sun.mode', s.mode, ['datetime', 'manual']);
  date('sun.isoDate', s.isoDate);
  number('sun.lat', s.lat, -90, 90);
  number('sun.lon', s.lon, -180, 180);
  number('sun.azimuthDeg', s.azimuthDeg, -360, 360);
  number('sun.elevationDeg', s.elevationDeg, -90, 90);
  number('sun.northRotationDeg', s.northRotationDeg, -360, 360);
  number('sun.intensity', s.intensity, 0);
  number('sun.colorTempK', s.colorTempK, 1000, 40000);
  const a = view.ambient;
  number('ambient.intensity', a.intensity, 0);
  number('ambient.hdriIntensity', a.hdriIntensity, 0);
  number('ambient.hdriRotationDeg', a.hdriRotationDeg, -360, 360);
  choice('ambient.background', a.background, ['sky', 'color', 'hdri']);
  for (const key of ['skyColor', 'groundColor', 'backgroundColor'] as const)
    color(`ambient.${key}`, a[key]);
  if (a.hdri !== undefined && typeof a.hdri !== 'string')
    fail('ambient.hdri', 'se esperaba un nombre');
  const sh = view.shadows;
  bool('shadows.enabled', sh.enabled);
  choice('shadows.type', sh.type, ['pcf', 'vsm']);
  choice('shadows.fit', sh.fit, ['auto', 'scene', 'view']);
  if (
    !Number.isInteger(sh.mapSize) ||
    sh.mapSize < 256 ||
    sh.mapSize > 8192 ||
    sh.mapSize & (sh.mapSize - 1)
  )
    fail('shadows.mapSize', 'debe ser una potencia de 2 entre 256 y 8192');
  number('shadows.bias', sh.bias, -1, 1);
  number('shadows.normalBias', sh.normalBias, 0);
  number('shadows.radius', sh.radius, 0, 100);
  bool('ground.enabled', view.ground.enabled);
  color('ground.color', view.ground.color);
  choice('tonemapping.type', view.tonemapping.type, ['aces', 'agx', 'neutral', 'none']);
  number('tonemapping.exposure', view.tonemapping.exposure, 0);
  const ids = new Set<string>();
  for (const [j, l] of view.lights.entries()) {
    const p = `lights[${j}]`;
    if (ids.has(l.id)) fail(`${p}.id`, 'identificador duplicado');
    ids.add(l.id);
    vector(`${p}.position`, l.position);
    if (l.target !== undefined) vector(`${p}.target`, l.target);
    if (l.type !== 'point') {
      const target = l.target ?? [0, 0, 0];
      if (Math.hypot(...target.map((n, i) => n - l.position[i]!)) < 1e-9)
        fail(`${p}.target`, 'debe ser distinto de position');
    }
    color(`${p}.color`, l.color);
    number(`${p}.intensity`, l.intensity, 0);
    if (l.distance !== undefined) number(`${p}.distance`, l.distance, 0);
    if (l.angleDeg !== undefined) {
      number(`${p}.angleDeg`, l.angleDeg, 0, 90);
      if (l.angleDeg <= 0 || l.angleDeg >= 90)
        fail(`${p}.angleDeg`, 'debe estar entre 0 y 90, excluidos');
    }
    if (l.width !== undefined) positive(`${p}.width`, l.width);
    if (l.height !== undefined) positive(`${p}.height`, l.height);
  }
  for (const [name, value] of Object.entries(view.layers)) {
    const state = requireObject(value, `${path}.layers[${JSON.stringify(name)}]`);
    bool(`layers[${JSON.stringify(name)}].visible`, state['visible'] as boolean);
    if (state['colorOverride'] !== undefined)
      color(`layers[${JSON.stringify(name)}].colorOverride`, state['colorOverride'] as string);
  }
  date('createdAt', view.createdAt);
}

/**
 * Lee y valida un views.json (entrada no confiable). Comprueba la estructura mínima que
 * el visor necesita; descarta miniaturas que no sean imágenes data: y campos sobrantes de
 * `cameraWorld` (se recalculan al exportar).
 */
export function parseViewsFile(text: string): { views: SceneView[]; originOffset: Vec3 | null } {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new ViewsFileError('El archivo no es JSON válido.');
  }
  const root = requireObject(raw, 'views.json');
  if (root['schemaVersion'] !== VIEWS_SCHEMA_VERSION) {
    throw new ViewsFileError(
      `schemaVersion ${String(root['schemaVersion'])} no soportada (se espera ${VIEWS_SCHEMA_VERSION}).`,
    );
  }
  if (!Array.isArray(root['views'])) throw new ViewsFileError('views: se esperaba una lista.');
  const system =
    root['coordinateSystem'] === undefined
      ? undefined
      : requireObject(root['coordinateSystem'], 'coordinateSystem');
  if (system?.['originOffset'] !== undefined && !isVec3(system['originOffset']))
    throw new ViewsFileError(
      'coordinateSystem.originOffset: se esperaba un vector finito [x, y, z]',
    );
  const originOffset = isVec3(system?.['originOffset']) ? system['originOffset'] : null;

  const views = root['views'].map((item, i): SceneView => {
    const path = `views[${i}]`;
    const v = requireObject(item, path);
    const camera = requireObject(v['camera'], `${path}.camera`);
    for (const key of ['position', 'target', 'up'] as const) {
      if (!isVec3(camera[key]))
        throw new ViewsFileError(`${path}.camera.${key}: se esperaba [x, y, z]`);
    }
    if (camera['type'] !== 'perspective' && camera['type'] !== 'orthographic') {
      throw new ViewsFileError(`${path}.camera.type: perspective u orthographic`);
    }
    const render = requireObject(v['render'], `${path}.render`);
    const width = requireNumber(render, 'width', `${path}.render`);
    const height = requireNumber(render, 'height', `${path}.render`);
    const sun = requireObject(v['sun'], `${path}.sun`);
    requireNumber(sun, 'azimuthDeg', `${path}.sun`);
    requireNumber(sun, 'elevationDeg', `${path}.sun`);
    if (!Array.isArray(v['lights']))
      throw new ViewsFileError(`${path}.lights: se esperaba una lista`);
    for (const [j, light] of (v['lights'] as unknown[]).entries()) {
      const l = requireObject(light, `${path}.lights[${j}]`);
      if (!['point', 'spot', 'area'].includes(String(l['type']))) {
        throw new ViewsFileError(`${path}.lights[${j}].type no válido`);
      }
      if (!isVec3(l['position']))
        throw new ViewsFileError(`${path}.lights[${j}].position no válida`);
    }
    requireObject(v['layers'], `${path}.layers`);
    const thumbnail = v['thumbnailDataUrl'];
    const view = { ...v } as unknown as SceneView & { cameraWorld?: unknown };
    delete view.cameraWorld;
    view.id = typeof v['id'] === 'string' && v['id'] ? v['id'] : crypto.randomUUID();
    view.name = typeof v['name'] === 'string' && v['name'].trim() ? v['name'] : `Vista ${i + 1}`;
    view.render = { width, height, aspect: '' }; // calcular solo después de validar dimensiones
    if (typeof thumbnail !== 'string' || !DATA_IMAGE.test(thumbnail)) delete view.thumbnailDataUrl;
    // Secciones ausentes o incompletas (archivos antiguos o editados a mano): valores por defecto
    const defaults = defaultState();
    for (const section of ['ambient', 'shadows', 'ground', 'tonemapping'] as const)
      if (v[section] !== undefined) requireObject(v[section], `${path}.${section}`);
    view.camera = { ...defaults.camera, ...view.camera };
    view.sun = { ...defaults.sun, ...view.sun };
    view.ambient = { ...defaults.ambient, ...view.ambient };
    view.shadows = { ...defaults.shadows, ...view.shadows };
    view.ground = { ...defaults.ground, ...view.ground };
    view.tonemapping = { ...defaults.tonemapping, ...view.tonemapping };
    view.lights = view.lights.map((l) => ({
      ...l,
      id: typeof l.id === 'string' ? l.id : crypto.randomUUID(),
    }));
    if (v['createdAt'] !== undefined && typeof v['createdAt'] !== 'string')
      throw new ViewsFileError(`${path}.createdAt: se esperaba una fecha ISO`);
    view.createdAt = typeof v['createdAt'] === 'string' ? v['createdAt'] : new Date().toISOString();
    validateView(view, path);
    view.render.aspect = aspectLabel(width, height);
    return view;
  });
  return { views, originOffset };
}
