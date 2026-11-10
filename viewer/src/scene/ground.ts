import { type Box3, Mesh, MeshStandardMaterial, PlaneGeometry, Vector3 } from 'three';
import type { GroundState } from '../state';

/** El suelo se extiende más allá del modelo para recoger sombras largas al atardecer. */
const GROUND_EXTENT_FACTOR = 4;
/** Ligeramente por debajo de la base para no competir en profundidad con viales y agua. */
const GROUND_OFFSET_M = 0.01;

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
    this.mesh.position.set(center.x, box.min.y - GROUND_OFFSET_M, center.z);
  }

  apply(state: GroundState): void {
    this.mesh.visible = state.enabled;
    this.mesh.material.color.set(state.color);
  }
}
