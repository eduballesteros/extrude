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

/** Coordenadas reales del DWG → escena. */
export function dwgToScene(p: Vec3, origin: Vec3): Vec3 {
  return [p[0] - origin[0], p[2] - origin[2], -(p[1] - origin[1])];
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
  const system = root['coordinateSystem'] as Record<string, unknown> | undefined;
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
    view.render = { width, height, aspect: aspectLabel(width, height) };
    if (typeof thumbnail !== 'string' || !DATA_IMAGE.test(thumbnail)) delete view.thumbnailDataUrl;
    // Secciones ausentes o incompletas (archivos antiguos o editados a mano): valores por defecto
    const defaults = defaultState();
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
    view.createdAt = typeof v['createdAt'] === 'string' ? v['createdAt'] : new Date().toISOString();
    return view;
  });
  return { views, originOffset };
}
