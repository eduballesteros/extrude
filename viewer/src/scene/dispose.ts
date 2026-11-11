import type { BufferGeometry, Material, Object3D, Texture } from 'three';

interface Disposable {
  geometry?: BufferGeometry;
  material?: Material | Material[];
  /** InstancedMesh / BatchedMesh: libera sus búferes propios (instanceMatrix, colores…). */
  dispose?: () => void;
  isInstancedMesh?: boolean;
  isBatchedMesh?: boolean;
}

function texturesOf(material: Material): Texture[] {
  const found: Texture[] = [];
  for (const value of Object.values(material)) {
    if ((value as Texture | null)?.isTexture) found.push(value as Texture);
  }
  return found;
}

/**
 * Libera de la GPU geometrías, materiales y texturas de `root` (mallas, líneas, puntos e
 * instancias). Cada recurso compartido se libera una sola vez. `keep` excluye texturas que no son
 * del modelo (entorno de la escena), por si algún material las referencia.
 */
export function disposeObject(root: Object3D, keep: ReadonlySet<Texture> = new Set()): void {
  const geometries = new Set<BufferGeometry>();
  const materials = new Set<Material>();
  root.traverse((o) => {
    const item = o as Object3D & Disposable;
    if (item.geometry) geometries.add(item.geometry);
    if (item.material) {
      for (const m of Array.isArray(item.material) ? item.material : [item.material]) {
        materials.add(m);
      }
    }
    if (item.isInstancedMesh || item.isBatchedMesh) item.dispose?.();
  });
  const textures = new Set<Texture>();
  for (const material of materials) {
    for (const texture of texturesOf(material)) if (!keep.has(texture)) textures.add(texture);
  }
  for (const geometry of geometries) geometry.dispose();
  for (const material of materials) material.dispose();
  for (const texture of textures) texture.dispose();
}
