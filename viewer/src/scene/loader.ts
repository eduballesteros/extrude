import { type Group, LoadingManager } from 'three';
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
}

const DRACO_PATH = `${import.meta.env.BASE_URL}draco/`;

function createLoader(manager?: LoadingManager): GLTFLoader {
  const draco = new DRACOLoader(manager).setDecoderPath(DRACO_PATH);
  return new GLTFLoader(manager).setDRACOLoader(draco).setMeshoptDecoder(MeshoptDecoder);
}

function toModel(gltf: GLTF, name: string): LoadedModel {
  const userData = gltf.scene.userData as Partial<SceneExtras>;
  const extras = 'origin_offset' in userData ? (userData as SceneExtras) : null;
  return { root: gltf.scene, extras, name };
}

export async function loadFromUrl(url: string): Promise<LoadedModel> {
  const gltf = await createLoader().loadAsync(url);
  return toModel(gltf, url.split('/').pop() ?? url);
}

export async function loadFromFile(file: File): Promise<LoadedModel> {
  const buffer = await file.arrayBuffer();
  const gltf = await createLoader().parseAsync(buffer, '');
  return toModel(gltf, file.name);
}
