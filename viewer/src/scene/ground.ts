import { type Box3, Mesh, MeshStandardMaterial, PlaneGeometry, Vector3 } from 'three';
import type { GroundState } from '../state';

/** El suelo se extiende más allá del modelo para recoger sombras largas al atardecer. */
const GROUND_EXTENT_FACTOR = 4;
/**
 * Por debajo de la base para no competir en profundidad (z-fighting) con viales, agua o
 * terreno plano: proporcional a la escena, porque la precisión de profundidad también lo es.
 */
const GROUND_OFFSET_MIN_M = 0.01;
const GROUND_OFFSET_FRACTION = 2e-4;

export class Ground {
  readonly mesh: Mesh<PlaneGeometry, MeshStandardMaterial>;

  constructor() {
    this.mesh = new Mesh(
      new PlaneGeometry(1, 1),
      new MeshStandardMaterial({ roughness: 1, metalness: 0 }),
    );
    this.mesh.rotation.x = -Math.PI / 2;
    this.mesh.receiveShadow = true;
    this.mesh.name = 'extrude:ground';
  }

  fit(box: Box3): void {
    const size = box.getSize(new Vector3());
    const center = box.getCenter(new Vector3());
    const extent = Math.max(size.x, size.z, 1) * GROUND_EXTENT_FACTOR;
    this.mesh.scale.set(extent, extent, 1);
    const offset = Math.max(GROUND_OFFSET_MIN_M, size.length() * GROUND_OFFSET_FRACTION);
    this.mesh.position.set(center.x, box.min.y - offset, center.z);
  }

  apply(state: GroundState): void {
    this.mesh.visible = state.enabled;
    this.mesh.material.color.set(state.color);
  }
}
