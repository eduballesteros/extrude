import { Color, type Mesh, type MeshStandardMaterial, type Object3D } from 'three';
import type { LayerState } from '../state';

export interface LayerInfo {
  name: string;
  kind: string;
  /** Nodo de la capa en cada GLB: uno, o uno por tesela en un conjunto teselado. */
  nodes: Object3D[];
  triangles: number;
  materials: MeshStandardMaterial[];
  originalColors: Color[];
}

interface LayerExtras {
  layer?: string;
  kind?: string;
  visible?: boolean;
  triangle_count?: number;
}

function materialsOf(node: Object3D): MeshStandardMaterial[] {
  const found = new Set<MeshStandardMaterial>();
  node.traverse((o) => {
    const mesh = o as Mesh;
    if (!mesh.material) return;
    for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      if ('color' in m) found.add(m as MeshStandardMaterial);
    }
  });
  return [...found];
}

function insideAny(node: Object3D, ancestors: Object3D[]): boolean {
  for (let p = node.parent; p; p = p.parent) if (ancestors.includes(p)) return true;
  return false;
}

/**
 * Capas del GLB del pipeline: un nodo por capa con `extras.layer` (userData en Three.js). En un
 * conjunto teselado cada tesela trae su propio nodo de la misma capa: se agrupan todos.
 */
export class LayerManager {
  readonly layers = new Map<string, LayerInfo>();

  attach(root: Object3D): void {
    this.layers.clear();
    root.traverse((node) => {
      const extras = node.userData as LayerExtras;
      if (typeof extras.layer !== 'string') return;
      const info = this.layers.get(extras.layer);
      if (info && insideAny(node, info.nodes)) return; // subnodo de una capa ya registrada
      const materials = materialsOf(node).filter((m) => !info?.materials.includes(m));
      if (!info) {
        this.layers.set(extras.layer, {
          name: extras.layer,
          kind: extras.kind ?? 'unknown',
          nodes: [node],
          triangles: extras.triangle_count ?? 0,
          materials,
          originalColors: materials.map((m) => m.color.clone()),
        });
        return;
      }
      info.nodes.push(node);
      info.triangles += extras.triangle_count ?? 0;
      info.materials.push(...materials);
      info.originalColors.push(...materials.map((m) => m.color.clone()));
    });
  }

  /** Estado inicial según `extras.visible` de cada capa. */
  initialState(): Record<string, LayerState> {
    const state: Record<string, LayerState> = Object.create(null) as Record<string, LayerState>;
    for (const info of this.layers.values()) {
      state[info.name] = { visible: (info.nodes[0]!.userData as LayerExtras).visible !== false };
    }
    return state;
  }

  apply(states: Record<string, LayerState>): void {
    for (const info of this.layers.values()) {
      const state = Object.hasOwn(states, info.name) ? states[info.name] : undefined;
      for (const node of info.nodes) node.visible = state?.visible ?? true;
      info.materials.forEach((material, i) => {
        material.color.copy(
          state?.colorOverride ? new Color(state.colorOverride) : info.originalColors[i]!,
        );
      });
    }
  }

  /** Deja visible solo `name`. */
  static isolate(states: Record<string, LayerState>, name: string): void {
    for (const [layer, state] of Object.entries(states)) state.visible = layer === name;
  }

  static showAll(states: Record<string, LayerState>): void {
    for (const state of Object.values(states)) state.visible = true;
  }
}
