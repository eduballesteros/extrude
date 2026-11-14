import type { DataTexture } from 'three';

/** Carga un HDRI equirectangular (.hdr o .exr) desde un archivo local. */
export async function loadHdri(file: File): Promise<DataTexture> {
  const extension = file.name.toLowerCase().split('.').pop();
  if (extension !== 'hdr' && extension !== 'exr') {
    throw new Error(`Formato no soportado: .${extension ?? '?'} (usa .hdr o .exr)`);
  }
  const url = URL.createObjectURL(file);
  try {
    // Los cargadores (y su descompresor) se descargan solo al elegir un HDRI
    const loader =
      extension === 'exr'
        ? new (await import('three/examples/jsm/loaders/EXRLoader.js')).EXRLoader()
        : new (await import('three/examples/jsm/loaders/HDRLoader.js')).HDRLoader();
    return await loader.loadAsync(url);
  } finally {
    URL.revokeObjectURL(url);
  }
}
