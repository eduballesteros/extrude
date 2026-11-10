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

async function parse(bytes: ArrayBuffer, name: string): Promise<LoadedModel> {
  const gltf = await createLoader().parseAsync(bytes, '');
  return toModel(gltf, name, bytes);
}

export async function loadFromUrl(url: string, name?: string): Promise<LoadedModel> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status} al descargar ${url}`);
  return parse(
    await response.arrayBuffer(),
    name ?? decodeURIComponent(url.split('/').pop() ?? url),
  );
}

export async function loadFromFile(file: File): Promise<LoadedModel> {
  return parse(await file.arrayBuffer(), file.name);
}
