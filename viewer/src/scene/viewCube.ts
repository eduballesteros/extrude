/**
 * Geometría del cubo de orientación (ViewCube): proyección de sus caras con la rotación de la
 * cámara y dirección del norte real en pantalla. Sin DOM: se testea en Node.
 */
import { type Quaternion, Vector3 } from 'three';
import type { Vec3 } from '../state';
import type { ViewPreset } from './navigation';

export interface CubeFaceDef {
  id: Exclude<ViewPreset, 'iso'>;
  label: string;
  normal: Vec3;
}

/** Caras del cubo en coordenadas de la escena (Y arriba; norte del dibujo = −Z). */
export const CUBE_FACES: CubeFaceDef[] = [
  { id: 'top', label: 'TOP', normal: [0, 1, 0] },
  { id: 'north', label: 'BACK', normal: [0, 0, -1] },
  { id: 'south', label: 'FRONT', normal: [0, 0, 1] },
  { id: 'east', label: 'RIGHT', normal: [1, 0, 0] },
  { id: 'west', label: 'LEFT', normal: [-1, 0, 0] },
];

export interface ProjectedFace {
  id: CubeFaceDef['id'];
  label: string;
  /** Vértices en píxeles relativos al centro (x a la derecha, y hacia abajo). */
  points: [number, number][];
  center: [number, number];
  /** Profundidad media en espacio de vista (mayor = más cerca de la cámara). */
  depth: number;
}

function corners(normal: Vec3): Vector3[] {
  const n = new Vector3(...normal);
  // Dos ejes perpendiculares a la normal, de lado 1 (cubo de −0.5 a 0.5)
  const u = Math.abs(n.y) > 0.5 ? new Vector3(1, 0, 0) : new Vector3(0, 1, 0);
  const v = new Vector3().crossVectors(n, u);
  const c = n.clone().multiplyScalar(0.5);
  return [
    c.clone().addScaledVector(u, 0.5).addScaledVector(v, 0.5),
    c.clone().addScaledVector(u, 0.5).addScaledVector(v, -0.5),
    c.clone().addScaledVector(u, -0.5).addScaledVector(v, -0.5),
    c.clone().addScaledVector(u, -0.5).addScaledVector(v, 0.5),
  ];
}

/** Caras visibles desde la cámara, ordenadas de la más lejana a la más cercana. */
export function projectCube(camera: Quaternion, size: number): ProjectedFace[] {
  const toView = camera.clone().invert();
  const faces: ProjectedFace[] = [];
  for (const face of CUBE_FACES) {
    const normal = new Vector3(...face.normal).applyQuaternion(toView);
    if (normal.z <= 1e-3) continue; // de espaldas a la cámara
    const pts = corners(face.normal).map((p) => p.applyQuaternion(toView));
    const points = pts.map((p): [number, number] => [p.x * size, -p.y * size]);
    const center = new Vector3(...face.normal).multiplyScalar(0.5).applyQuaternion(toView);
    faces.push({
      id: face.id,
      label: face.label,
      points,
      center: [center.x * size, -center.y * size],
      depth: pts.reduce((sum, p) => sum + p.z, 0) / pts.length,
    });
  }
  return faces.sort((a, b) => a.depth - b.depth);
}

/**
 * Ángulo en pantalla (grados, 0 = arriba, horario) hacia el norte REAL: el norte del dibujo
 * girado `northRotationDeg` en sentido horario visto desde arriba.
 */
export function northScreenAngle(camera: Quaternion, northRotationDeg: number): number {
  const r = (northRotationDeg * Math.PI) / 180;
  const north = new Vector3(Math.sin(r), 0, -Math.cos(r));
  const flat = (v: Vector3): Vector3 => v.setY(0);
  // "Arriba en pantalla" sobre el suelo: en planta lo da el vector up de la cámara; en alzado,
  // la dirección a la que mira. Su suma funciona en cualquier inclinación (como una brújula).
  const up = flat(new Vector3(0, 1, 0).applyQuaternion(camera)).add(
    flat(new Vector3(0, 0, -1).applyQuaternion(camera)),
  );
  const right = flat(new Vector3(1, 0, 0).applyQuaternion(camera));
  if (up.lengthSq() < 1e-12 || right.lengthSq() < 1e-12) return 0;
  return (Math.atan2(north.dot(right.normalize()), north.dot(up.normalize())) * 180) / Math.PI;
}
