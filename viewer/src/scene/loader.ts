import { Group, LoadingManager } from 'three';
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js';
import { type GLTF, GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js';

/** Metadatos que el pipeline escribe en `scene.extras` (ver docs/EXPORT_FORMATS.md). */
export interface SceneExtras {
  origin_offset: [number, number, number];
  units: string;
  source_units?: string;
  crs: string | null;
  /** Centro de la escena en WGS84 (grados), si el CRS se pudo reproyectar. */
  center_lat?: number | null;
  center_lon?: number | null;
  axis_transform?: string;
  bbox: { min: [number, number, number]; max: [number, number, number] } | null;
  /** Revisión de la geometría construida (objects.json); falta en GLB anteriores. */
  geometry_revision?: string;
  /** Solo en un GLB materializado: revisión del plan de materiales aplicado. */
  material_revision?: string;
}

export interface LoadedModel {
  root: Group;
  extras: SceneExtras | null;
  name: string;
  /**
   * Bytes originales: se parchean para exportar el GLB con cámaras y luces. `null` en un
   * conjunto teselado (no hay un GLB único).
   */
  bytes: ArrayBuffer | null;
  /** Número de teselas si el modelo viene de un manifest.json (`extrude convert --tiles`). */
  tiles?: number;
}

/** Una tesela del manifest.json del pipeline (ver docs/EXPORT_FORMATS.md). */
export interface ManifestTile {
  id: string;
  /** Ruta relativa al manifest, p. ej. "tiles/tile_0_0.glb". */
  file: string;
  bbox: { min: [number, number, number]; max: [number, number, number] } | null;
  triangles: number;
  size_bytes: number;
}

export interface Manifest {
  schemaVersion: number;
  origin_offset: [number, number, number];
  units: string;
  crs: string | null;
  center_lat?: number | null;
  center_lon?: number | null;
  geometry_revision?: string;
  tile_size: number;
  tiles: ManifestTile[];
}

export const MANIFEST_SCHEMA_VERSION = 1;
const TILE_FILE = /^tiles\/[\w.-]+\.glb$/;

function isVec3(value: unknown): value is [number, number, number] {
  return Array.isArray(value) && value.length === 3 && value.every((v) => Number.isFinite(v));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isBbox(value: unknown): boolean {
  if (value === null) return true;
  return (
    isRecord(value) &&
    isVec3(value['min']) &&
    isVec3(value['max']) &&
    value['min'].every((n, i) => n <= (value['max'] as number[])[i]!)
  );
}

function isCount(value: unknown): boolean {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

/** Valida un manifest.json; las rutas de las teselas solo pueden estar dentro de tiles/. */
export function parseManifest(raw: unknown): Manifest {
  if (!isRecord(raw)) throw new Error('manifest.json vacío o no es un objeto.');
  const m = raw as Partial<Manifest>;
  if (m.schemaVersion !== MANIFEST_SCHEMA_VERSION) {
    throw new Error(`Versión de manifest no admitida: ${String(m.schemaVersion)}.`);
  }
  if (!isVec3(m.origin_offset)) throw new Error('manifest.json sin origin_offset válido.');
  if (m.units !== 'm') throw new Error('manifest.json: units debe ser "m".');
  if (m.crs !== null && typeof m.crs !== 'string') {
    throw new Error('manifest.json: crs debe ser texto o null.');
  }
  if (typeof m.tile_size !== 'number' || !Number.isFinite(m.tile_size) || m.tile_size <= 0) {
    throw new Error('manifest.json: tile_size debe ser finito y > 0.');
  }
  if (
    m.geometry_revision !== undefined &&
    (typeof m.geometry_revision !== 'string' || !/^sha256:[0-9a-f]{64}$/.test(m.geometry_revision))
  ) {
    throw new Error('manifest.json: geometry_revision no válida.');
  }
  for (const [key, limit] of [
    ['center_lat', 90],
    ['center_lon', 180],
  ] as const) {
    const value = m[key];
    if (
      value != null &&
      (typeof value !== 'number' || !Number.isFinite(value) || Math.abs(value) > limit)
    ) {
      throw new Error(`manifest.json: ${key} fuera de rango.`);
    }
  }
  if (!Array.isArray(m.tiles) || m.tiles.length === 0) {
    throw new Error('manifest.json no contiene teselas.');
  }
  const ids = new Set<string>();
  const files = new Set<string>();
  for (const [index, tile] of m.tiles.entries()) {
    const field = `manifest.json: tiles[${index}]`;
    if (!isRecord(tile)) throw new Error(`${field} no es un objeto.`);
    if (typeof tile?.file !== 'string' || !TILE_FILE.test(tile.file) || tile.file.includes('..')) {
      throw new Error(`Ruta de tesela no válida en manifest.json: ${String(tile?.file)}`);
    }
    if (typeof tile.id !== 'string' || !/^[\w-]+$/.test(tile.id) || ids.has(tile.id)) {
      throw new Error(`${field}.id no válido o duplicado.`);
    }
    if (files.has(tile.file)) throw new Error(`${field}.file duplicado.`);
    if (!isBbox(tile.bbox)) throw new Error(`${field}.bbox no válido.`);
    if (!isCount(tile.triangles)) throw new Error(`${field}.triangles no válido.`);
    if (!isCount(tile.size_bytes)) throw new Error(`${field}.size_bytes no válido.`);
    ids.add(tile.id);
    files.add(tile.file);
  }
  return m as Manifest;
}

/** Caja que envuelve las de todas las teselas (coordenadas glTF ya recentradas). */
export function unionBbox(tiles: ManifestTile[]): SceneExtras['bbox'] {
  const boxes = tiles.map((t) => t.bbox).filter((b): b is NonNullable<typeof b> => b !== null);
  if (!boxes.length) return null;
  const min = [0, 1, 2].map((i) => Math.min(...boxes.map((b) => b.min[i]!)));
  const max = [0, 1, 2].map((i) => Math.max(...boxes.map((b) => b.max[i]!)));
  return {
    min: min as [number, number, number],
    max: max as [number, number, number],
  };
}

/** Metadatos de escena del conjunto: todas las teselas comparten origen, unidades y CRS. */
export function manifestExtras(manifest: Manifest): SceneExtras {
  return {
    origin_offset: manifest.origin_offset,
    units: manifest.units,
    crs: manifest.crs,
    center_lat: manifest.center_lat ?? null,
    center_lon: manifest.center_lon ?? null,
    bbox: unionBbox(manifest.tiles),
    ...(manifest.geometry_revision ? { geometry_revision: manifest.geometry_revision } : {}),
  };
}

export type LoadStage = 'download' | 'read' | 'parse' | 'prepare';

/** `fraction` en [0, 1], o null si no se puede medir (fase indeterminada). */
export type ProgressCallback = (stage: LoadStage, fraction: number | null) => void;

export class ModelDownloadError extends Error {
  constructor(
    readonly status: number,
    url: string,
  ) {
    super(`HTTP ${status} al descargar ${url}`);
  }
}

const DRACO_PATH = `${import.meta.env.BASE_URL}draco/`;

function createLoader(local = false): GLTFLoader {
  const manager = new LoadingManager();
  if (local)
    manager.setURLModifier((url) => {
      if (!/^(data:|blob:)/i.test(url)) {
        throw new Error(
          'El glTF local referencia recursos externos. Usa un GLB autocontenido o abre el modelo mediante una URL que sirva también sus buffers y texturas.',
        );
      }
      return url;
    });
  const draco = new DRACOLoader().setDecoderPath(DRACO_PATH);
  return new GLTFLoader(manager).setDRACOLoader(draco).setMeshoptDecoder(MeshoptDecoder);
}

function resourceBase(url: string): string {
  const absolute = new URL(url, globalThis.location?.href);
  return ['data:', 'blob:'].includes(absolute.protocol) ? '' : new URL('.', absolute).href;
}

function toModel(gltf: GLTF, name: string, bytes: ArrayBuffer): LoadedModel {
  const userData = gltf.scene.userData as Partial<SceneExtras>;
  const extras = 'origin_offset' in userData ? (userData as SceneExtras) : null;
  return { root: gltf.scene, extras, name, bytes };
}

/** Cede un fotograma para que la interfaz pinte el estado antes de una tarea pesada. */
export function nextFrame(): Promise<void> {
  return new Promise((resolve) => requestAnimationFrame(() => resolve()));
}

async function parse(
  bytes: ArrayBuffer,
  name: string,
  onProgress?: ProgressCallback,
  base = '',
): Promise<LoadedModel> {
  onProgress?.('parse', null);
  await nextFrame();
  const gltf = await createLoader(!base).parseAsync(bytes, base);
  return toModel(gltf, name, bytes);
}

/**
 * Descarga leyendo el cuerpo por fragmentos para informar del progreso real
 * (si el servidor envía Content-Length; si no, la fase es indeterminada).
 */
export async function downloadResource(
  url: string,
  onProgress?: ProgressCallback,
  signal?: AbortSignal,
): Promise<{ bytes: ArrayBuffer; url: string }> {
  signal?.throwIfAborted();
  const response = await (signal ? fetch(url, { signal }) : fetch(url));
  if (!response.ok) throw new ModelDownloadError(response.status, url);
  const total = Number(response.headers.get('Content-Length')) || 0;
  const resolvedUrl = response.url || url;
  if (!response.body) return { bytes: await response.arrayBuffer(), url: resolvedUrl };
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  onProgress?.('download', total ? 0 : null);
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    onProgress?.('download', total ? Math.min(received / total, 1) : null);
  }
  const bytes = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return { bytes: bytes.buffer, url: resolvedUrl };
}

export async function loadFromUrl(
  url: string,
  name?: string,
  onProgress?: ProgressCallback,
  signal?: AbortSignal,
): Promise<LoadedModel> {
  const resource = await downloadResource(url, onProgress, signal);
  return parse(
    resource.bytes,
    name ?? decodeURIComponent(url.split('/').pop() ?? url),
    onProgress,
    resourceBase(resource.url),
  );
}

export async function loadFromFile(
  file: File,
  onProgress?: ProgressCallback,
): Promise<LoadedModel> {
  onProgress?.('read', null);
  return parse(await file.arrayBuffer(), file.name, onProgress);
}

/**
 * Carga un conjunto teselado: descarga el manifest.json y todas sus teselas (rutas relativas al
 * manifest) y agrupa sus raíces en un único Group. Como comparten `origin_offset`, se colocan
 * juntas sin desplazarlas. El progreso de descarga se mide con `size_bytes` del manifest.
 */
export async function loadFromManifest(
  url: string,
  name?: string,
  onProgress?: ProgressCallback,
  signal?: AbortSignal,
): Promise<LoadedModel> {
  onProgress?.('download', null);
  signal?.throwIfAborted();
  const response = await (signal ? fetch(url, { signal }) : fetch(url));
  if (!response.ok) throw new ModelDownloadError(response.status, url);
  const manifest = parseManifest(await response.json());
  const total = manifest.tiles.reduce((sum, t) => sum + (t.size_bytes || 0), 0);
  let done = 0;
  const buffers: { bytes: ArrayBuffer; url: string }[] = [];
  const manifestUrl = response.url || url;
  for (const tile of manifest.tiles) {
    const tileUrl = new URL(tile.file, new URL(manifestUrl, globalThis.location?.href)).toString();
    buffers.push(
      await downloadResource(
        tileUrl,
        (_stage, fraction) => {
          if (!total || fraction === null) return onProgress?.('download', null);
          onProgress?.('download', Math.min((done + fraction * tile.size_bytes) / total, 1));
        },
        signal,
      ),
    );
    done += tile.size_bytes || 0;
  }
  onProgress?.('parse', null);
  await nextFrame();
  const loader = createLoader();
  const root = new Group();
  root.name = name ?? 'tiles';
  for (const [i, resource] of buffers.entries()) {
    const gltf = await loader.parseAsync(resource.bytes, resourceBase(resource.url));
    gltf.scene.name = manifest.tiles[i]!.id;
    root.add(gltf.scene);
  }
  const extras = manifestExtras(manifest);
  root.userData = { ...extras };
  return {
    root,
    extras,
    name: name ?? 'tiles',
    bytes: null,
    tiles: manifest.tiles.length,
  };
}

/** El modelo es un manifest de teselas (por la URL). */
export function isManifestUrl(url: string): boolean {
  return /(^|\/)manifest\.json$/i.test(new URL(url, 'http://x/').pathname);
}
