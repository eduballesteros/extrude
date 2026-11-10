import type { DataTexture } from 'three';
import { EXRLoader } from 'three/examples/jsm/loaders/EXRLoader.js';
import { HDRLoader } from 'three/examples/jsm/loaders/HDRLoader.js';

/** Carga un HDRI equirectangular (.hdr o .exr) desde un archivo local. */
export async function loadHdri(file: File): Promise<DataTexture> {
  const extension = file.name.toLowerCase().split('.').pop();
  if (extension !== 'hdr' && extension !== 'exr') {
    throw new Error(`Formato no soportado: .${extension ?? '?'} (usa .hdr o .exr)`);
  }
  const url = URL.createObjectURL(file);
  try {
    const loader = extension === 'exr' ? new EXRLoader() : new HDRLoader();
    return await loader.loadAsync(url);
  } finally {
    URL.revokeObjectURL(url);
  }
}
