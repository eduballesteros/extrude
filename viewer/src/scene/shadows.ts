import { Box3, Matrix4, Vector3 } from 'three';

/**
 * Tamaño (diagonal) a partir del cual `fit: auto` ajusta la sombra solo a la zona visible:
 * un shadow map de 4096 px sobre 1,5 km da ~0,4 m por texel, ya visiblemente borroso.
 */
export const AUTO_VIEW_FIT_DIAGONAL_M = 1500;
/** Radio de la zona visible respecto a la distancia cámara–objetivo. */
export const VIEW_FIT_RADIUS_FACTOR = 1.6;
/** Profundidad extra tras la caja (en radios): sombras de hasta ~6 radios con sol bajo. */
const SHADOW_FAR_EXTENSION = 6;

export interface ShadowFrustum {
  position: Vector3;
  target: Vector3;
  /** Vector "arriba" de la cámara de sombra (asígnalo a shadow.camera.up: en el cénit cambia). */
  up: Vector3;
  left: number;
  right: number;
  top: number;
  bottom: number;
  near: number;
  far: number;
}

const UP = new Vector3(0, 1, 0);
const ALT_UP = new Vector3(0, 0, -1);

function corners(box: Box3): Vector3[] {
  const out: Vector3[] = [];
  for (const x of [box.min.x, box.max.x])
    for (const y of [box.min.y, box.max.y])
      for (const z of [box.min.z, box.max.z]) out.push(new Vector3(x, y, z));
  return out;
}

/**
 * Cámara ortográfica de una luz direccional que contiene `box` entero.
 * `toSun` es el vector unitario hacia el sol. Con `mapSize` se redondean los límites a la
 * rejilla de texels, para que las sombras no "naden" al mover la cámara (ajuste a la vista).
 */
export function fitShadow(box: Box3, toSun: Vector3, mapSize?: number): ShadowFrustum {
  const center = box.getCenter(new Vector3());
  const radius = Math.max(box.getSize(new Vector3()).length() / 2, 1e-3);
  const position = center.clone().addScaledVector(toSun, radius * 2);
  const up = Math.abs(toSun.dot(UP)) > 0.999 ? ALT_UP : UP;
  const view = new Matrix4().lookAt(position, center, up).setPosition(position).invert();

  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  let minZ = Infinity;
  let maxZ = -Infinity;
  for (const corner of corners(box)) {
    const p = corner.applyMatrix4(view);
    minX = Math.min(minX, p.x);
    maxX = Math.max(maxX, p.x);
    minY = Math.min(minY, p.y);
    maxY = Math.max(maxY, p.y);
    minZ = Math.min(minZ, p.z);
    maxZ = Math.max(maxZ, p.z);
  }
  if (mapSize) {
    const texel = Math.max(maxX - minX, maxY - minY) / mapSize;
    minX = Math.floor(minX / texel) * texel;
    minY = Math.floor(minY / texel) * texel;
    maxX = Math.ceil(maxX / texel) * texel;
    maxY = Math.ceil(maxY / texel) * texel;
  }
  // La cámara mira hacia −Z: near/far son distancias positivas. El far se alarga para que el
  // suelo fuera de la caja, donde caen las sombras largas del atardecer, quede dentro.
  const margin = radius * 0.01;
  return {
    position,
    target: center,
    up: up.clone(),
    left: minX,
    right: maxX,
    bottom: minY,
    top: maxY,
    near: Math.max(-maxZ - margin, 0.01),
    far: -minZ + radius * SHADOW_FAR_EXTENSION,
  };
}

/** Caja visible aproximada: alrededor del objetivo de la cámara, recortada a la escena. */
export function viewBox(scene: Box3, target: Vector3, cameraDistance: number): Box3 {
  const r = cameraDistance * VIEW_FIT_RADIUS_FACTOR;
  const around = new Box3(
    new Vector3(target.x - r, scene.min.y, target.z - r),
    new Vector3(target.x + r, scene.max.y, target.z + r),
  );
  const clipped = around.intersect(scene);
  return clipped.isEmpty() ? scene.clone() : clipped;
}

export function shouldFitToView(fit: 'auto' | 'scene' | 'view', scene: Box3): boolean {
  if (fit !== 'auto') return fit === 'view';
  return scene.getSize(new Vector3()).length() > AUTO_VIEW_FIT_DIAGONAL_M;
}
