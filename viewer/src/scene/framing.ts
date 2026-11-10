import { Box3, type Object3D, type PerspectiveCamera, Sphere, Vector3 } from 'three';
import type { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

/** Margen sobre la esfera envolvente para que el modelo no toque los bordes. */
const FIT_MARGIN = 1.2;
/** far/near máximo razonable para no perder precisión de profundidad. */
const MAX_DEPTH_RATIO = 50_000;

export function boundsOf(object: Object3D): Box3 {
  return new Box3().setFromObject(object);
}

/** Encuadra la cámara sobre `box` en vista 3/4 y ajusta near/far al tamaño de la escena. */
export function frameBox(camera: PerspectiveCamera, controls: OrbitControls, box: Box3): void {
  if (box.isEmpty()) return;
  const sphere = box.getBoundingSphere(new Sphere());
  const fov = (camera.fov * Math.PI) / 180;
  const distance = (sphere.radius * FIT_MARGIN) / Math.sin(fov / 2);
  const direction = new Vector3(1, 0.8, 1).normalize();
  camera.position.copy(sphere.center).addScaledVector(direction, distance);
  camera.far = distance + sphere.radius * 4;
  camera.near = Math.max(camera.far / MAX_DEPTH_RATIO, 0.01);
  camera.updateProjectionMatrix();
  controls.target.copy(sphere.center);
  controls.maxDistance = distance * 4;
  controls.update();
}
