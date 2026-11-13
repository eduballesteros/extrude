/**
 * Resalta objetos por ID persistente con su caja envolvente (propuesta de materiales de la IA,
 * candidatos de una referencia pendiente). Funciona con mallas fusionadas por capa (`_object_id`
 * indexa `userData.objects.id`) y con bloques instanciados (`userData.object_ids[instancia]`).
 * Solo dibuja líneas: no toca materiales ni geometría del modelo.
 */
import {
  Box3,
  Box3Helper,
  Color,
  Group,
  type InstancedMesh,
  type BufferAttribute,
  Matrix4,
  type Mesh,
  type Object3D,
  Vector3,
} from 'three';

function ancestorWith(object: Object3D, key: string): Object3D | null {
  let current: Object3D | null = object;
  while (current) {
    if (key in current.userData) return current;
    current = current.parent;
  }
  return null;
}

/** Cajas en coordenadas de mundo de cada ID pedido presente en `root`. */
export function objectBoxes(root: Object3D, ids: ReadonlySet<string>): Map<string, Box3> {
  const boxes = new Map<string, Box3>();
  if (!ids.size) return boxes;
  root.updateMatrixWorld(true);
  const point = new Vector3();
  const grow = (id: string, p: Vector3): void => {
    let box = boxes.get(id);
    if (!box) boxes.set(id, (box = new Box3()));
    box.expandByPoint(p);
  };
  root.traverse((node) => {
    const mesh = node as Mesh;
    if (!mesh.isMesh) return;
    const attributes = mesh.geometry.attributes as Record<string, BufferAttribute | undefined>;
    const index = attributes['_object_id'];
    const position = attributes['position'];
    if (!index || !position) return;
    const blockNode = ancestorWith(mesh, 'block');
    const instanced = (mesh as InstancedMesh).isInstancedMesh ? (mesh as InstancedMesh) : null;
    if (blockNode && instanced) {
      const all = blockNode.userData['object_ids'] as unknown;
      if (!Array.isArray(all)) return;
      // Caja local por parte del prototipo, transformada por cada instancia que interese
      const parts = new Map<number, Box3>();
      for (let i = 0; i < index.count; i++) {
        const part = Math.round(index.getX(i));
        let box = parts.get(part);
        if (!box) parts.set(part, (box = new Box3()));
        box.expandByPoint(point.fromBufferAttribute(position, i));
      }
      const matrix = new Matrix4();
      for (let n = 0; n < instanced.count; n++) {
        const row = all[n] as unknown;
        if (!Array.isArray(row)) continue;
        for (const [part, local] of parts) {
          const id: unknown = row[part];
          if (typeof id !== 'string' || !ids.has(id)) continue;
          instanced.getMatrixAt(n, matrix);
          matrix.premultiply(instanced.matrixWorld);
          const world = local.clone().applyMatrix4(matrix);
          grow(id, world.min);
          grow(id, world.max);
        }
      }
      return;
    }
    const layerNode = ancestorWith(mesh, 'objects');
    const table = (layerNode?.userData['objects'] as { id?: unknown[] } | undefined)?.id;
    if (!Array.isArray(table)) return;
    for (let i = 0; i < index.count; i++) {
      const id: unknown = table[Math.round(index.getX(i))];
      if (typeof id !== 'string' || !ids.has(id)) continue;
      grow(id, point.fromBufferAttribute(position, i).applyMatrix4(mesh.matrixWorld));
    }
  });
  return boxes;
}

export class ObjectHighlight {
  readonly group = new Group();
  private readonly color = new Color('#f59e0b');

  constructor() {
    this.group.name = 'extrude-highlight';
    this.group.renderOrder = 999;
  }

  /** Muestra las cajas de `ids` y devuelve cuántos objetos se encontraron. */
  show(root: Object3D | null, ids: Iterable<string>): number {
    this.clear();
    if (!root) return 0;
    const boxes = objectBoxes(root, new Set(ids));
    for (const box of boxes.values()) {
      box.expandByScalar(0.3);
      const helper = new Box3Helper(box, this.color);
      const material = helper.material as { depthTest: boolean; transparent: boolean };
      material.depthTest = false; // visible aunque otro edificio lo tape
      material.transparent = true;
      helper.renderOrder = 999;
      this.group.add(helper);
    }
    return boxes.size;
  }

  clear(): void {
    for (const child of [...this.group.children]) {
      const helper = child as Box3Helper;
      helper.geometry.dispose();
      (helper.material as { dispose(): void }).dispose();
      this.group.remove(helper);
    }
  }
}
