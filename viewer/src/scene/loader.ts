import type { Group } from 'three';
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js';
import { type GLTF, GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js';

/** Metadatos que el pipeline escribe en `scene.extras` (ver docs/EXPORT_FORMATS.md). */
export interface SceneExtras {
  origin_offset: [number, number, number];
  units: string;
  source_units?: string;
  crs: string | null;
  axis_transform?: string;
  bbox: { min: [number, number, number]; max: [number, number, number] } | null;
}

export interface LoadedModel {
  root: Group;
  extras: SceneExtras | null;
  name: string;
  /** Bytes originales: se parchean para exportar el GLB con cámaras y luces. */
  bytes: ArrayBuffer;
}

export type LoadStage = 'download' | 'read' | 'parse' | 'prepare';

/** `fraction` en [0, 1], o null si no se puede medir (fase indeterminada). */
export type ProgressCallback = (stage: LoadStage, fraction: number | null) => void;

const DRACO_PATH = `${import.meta.env.BASE_URL}draco/`;

function createLoader(): GLTFLoader {
  const draco = new DRACOLoader().setDecoderPath(DRACO_PATH);
  return new GLTFLoader().setDRACOLoader(draco).setMeshoptDecoder(MeshoptDecoder);
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
): Promise<LoadedModel> {
  onProgress?.('parse', null);
  await nextFrame();
  const gltf = await createLoader().parseAsync(bytes, '');
  return toModel(gltf, name, bytes);
}

/**
 * Descarga leyendo el cuerpo por fragmentos para informar del progreso real
 * (si el servidor envía Content-Length; si no, la fase es indeterminada).
 */
export async function download(url: string, onProgress?: ProgressCallback): Promise<ArrayBuffer> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status} al descargar ${url}`);
  const total = Number(response.headers.get('Content-Length')) || 0;
  if (!response.body) return response.arrayBuffer();
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
  return bytes.buffer;
}

export async function loadFromUrl(
  url: string,
  name?: string,
  onProgress?: ProgressCallback,
): Promise<LoadedModel> {
  const bytes = await download(url, onProgress);
  return parse(bytes, name ?? decodeURIComponent(url.split('/').pop() ?? url), onProgress);
}

export async function loadFromFile(
  file: File,
  onProgress?: ProgressCallback,
): Promise<LoadedModel> {
  onProgress?.('read', null);
  return parse(await file.arrayBuffer(), file.name, onProgress);
}
