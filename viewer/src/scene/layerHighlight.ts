/**
 * Resalta todos los elementos de una capa (selección en el panel de capas).
 *
 * No toca los materiales del modelo (pueden compartirse entre capas o llevar texturas): dibuja
 * encima una copia ligera de cada malla —misma geometría, misma matriz de mundo— con un tinte
 * translúcido. Vale para mallas fusionadas, bloques instanciados, líneas y conjuntos teselados
 * (una capa puede tener un nodo por tesela). Las copias no se pueden pulsar ni medir: el visor
 * solo lanza rayos contra el modelo.
 */
import {
  Group,
  InstancedMesh,
  type Line,
  LineBasicMaterial,
  LineSegments,
  type Material,
  Mesh,
  MeshBasicMaterial,
  type Object3D,
} from 'three';

export const LAYER_HIGHLIGHT_COLOR = '#e2783f';

function noRaycast(): void {
  /* ayuda visual: nunca es el objeto pulsado */
}

export class LayerHighlight {
  readonly group = new Group();
  private readonly fill: MeshBasicMaterial;
  private readonly stroke: LineBasicMaterial;

  constructor() {
    this.group.name = 'extrude-layer-highlight';
    this.group.renderOrder = 900;
    this.fill = new MeshBasicMaterial({
      color: LAYER_HIGHLIGHT_COLOR,
      transparent: true,
      opacity: 0.5,
      depthWrite: false,
      toneMapped: false,
      // Misma superficie que el modelo: sin esto, la copia compite en profundidad con él
      polygonOffset: true,
      polygonOffsetFactor: -2,
      polygonOffsetUnits: -2,
    });
    this.stroke = new LineBasicMaterial({
      color: LAYER_HIGHLIGHT_COLOR,
      transparent: true,
      depthTest: false,
      toneMapped: false,
    });
  }

  /** Resalta lo que cuelga de `nodes` (los nodos de la capa). Devuelve cuántas piezas dibuja. */
  show(nodes: readonly Object3D[]): number {
    this.clear();
    for (const node of nodes) {
      node.updateWorldMatrix(true, true);
      node.traverse((source) => {
        const copy = this.copyOf(source);
        if (!copy) return;
        copy.matrixAutoUpdate = false;
        copy.matrix.copy(source.matrixWorld);
        copy.matrixWorld.copy(source.matrixWorld);
        copy.frustumCulled = source.frustumCulled;
        copy.renderOrder = 900;
        copy.raycast = noRaycast;
        this.group.add(copy);
      });
    }
    return this.group.children.length;
  }

  private copyOf(source: Object3D): Object3D | null {
    const instanced = source as InstancedMesh;
    if (instanced.isInstancedMesh) {
      const copy = new InstancedMesh(instanced.geometry, this.fill, instanced.count);
      copy.instanceMatrix = instanced.instanceMatrix; // mismas copias, sin duplicar memoria
      copy.count = instanced.count;
      return copy;
    }
    const mesh = source as Mesh;
    if (mesh.isMesh) return new Mesh(mesh.geometry, this.fill);
    const line = source as Line;
    if (line.isLine) {
      const Type = line.constructor as new (g: Line['geometry'], m: Material) => Line;
      return (line as LineSegments).isLineSegments
        ? new LineSegments(line.geometry, this.stroke)
        : new Type(line.geometry, this.stroke);
    }
    return null;
  }

  get count(): number {
    return this.group.children.length;
  }

  /** Quita las copias. La geometría es del modelo: no se libera aquí. */
  clear(): void {
    for (const child of [...this.group.children]) this.group.remove(child);
  }

  dispose(): void {
    this.clear();
    this.fill.dispose();
    this.stroke.dispose();
  }
}
