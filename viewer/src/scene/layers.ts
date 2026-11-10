import { Color, type Mesh, type MeshStandardMaterial, type Object3D } from 'three';
import type { LayerState } from '../state';

export interface LayerInfo {
  name: string;
  kind: string;
  node: Object3D;
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

/** Capas del GLB del pipeline: un nodo por capa con `extras.layer` (userData en Three.js). */
export class LayerManager {
  readonly layers = new Map<string, LayerInfo>();

  attach(root: Object3D): void {
    this.layers.clear();
    root.traverse((node) => {
      const extras = node.userData as LayerExtras;
      if (typeof extras.layer !== 'string' || this.layers.has(extras.layer)) return;
      const materials = materialsOf(node);
      this.layers.set(extras.layer, {
        name: extras.layer,
        kind: extras.kind ?? 'unknown',
        node,
        triangles: extras.triangle_count ?? 0,
        materials,
        originalColors: materials.map((m) => m.color.clone()),
      });
    });
  }

  /** Estado inicial según `extras.visible` de cada capa. */
  initialState(): Record<string, LayerState> {
    const state: Record<string, LayerState> = {};
    for (const info of this.layers.values()) {
      state[info.name] = { visible: (info.node.userData as LayerExtras).visible !== false };
    }
    return state;
  }

  apply(states: Record<string, LayerState>): void {
    for (const info of this.layers.values()) {
      const state = states[info.name];
      info.node.visible = state?.visible ?? true;
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
