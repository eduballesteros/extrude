import type { Material, Mesh, Object3D, Texture } from 'three';

const MAP_SLOTS = ['map', 'normalMap', 'roughnessMap', 'metalnessMap', 'aoMap'] as const;

/**
 * Filtrado anisotrópico para las texturas del modelo (GLB materializado).
 *
 * Las fachadas se ven casi siempre en escorzo: con solo filtrado trilineal, las juntas finas del
 * ladrillo se convierten en vetas o moiré a media distancia. Devuelve cuántas texturas cambió.
 */
export function sharpenTextures(root: Object3D, maxAnisotropy: number): number {
  const seen = new Set<Texture>();
  root.traverse((o) => {
    const mesh = o as Mesh;
    if (!mesh.isMesh) return;
    const materials: Material[] = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const material of materials) {
      for (const slot of MAP_SLOTS) {
        const texture = (material as unknown as Record<string, Texture | null | undefined>)[slot];
        if (!texture || seen.has(texture)) continue;
        seen.add(texture);
        if (texture.anisotropy < maxAnisotropy) {
          texture.anisotropy = maxAnisotropy;
          texture.needsUpdate = true;
        }
      }
    }
  });
  return seen.size;
}
