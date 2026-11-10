import {
  type Camera,
  type Light,
  type Object3D,
  PointLight,
  PointLightHelper,
  RectAreaLight,
  type Scene,
  SpotLight,
  SpotLightHelper,
  Vector3,
} from 'three';
import type { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { TransformControls } from 'three/examples/jsm/controls/TransformControls.js';
import { RectAreaLightHelper } from 'three/examples/jsm/helpers/RectAreaLightHelper.js';
import { RectAreaLightUniformsLib } from 'three/examples/jsm/lights/RectAreaLightUniformsLib.js';
import type { LightState, LightType, Vec3 } from '../state';

const DEG = Math.PI / 180;
const HELPER_SIZE = 1;

interface Entry {
  light: Light;
  helper: Object3D & { update?: () => void; dispose?: () => void };
}

let rectAreaReady = false;

export function newLightState(type: LightType, position: Vec3, target: Vec3): LightState {
  const base = { id: crypto.randomUUID(), type, position, color: '#ffe8c0' };
  switch (type) {
    case 'point':
      return { ...base, intensity: 800, distance: 0 };
    case 'spot':
      return { ...base, target, intensity: 2000, distance: 0, angleDeg: 30 };
    case 'area':
      return { ...base, target, intensity: 10, width: 4, height: 2 };
  }
}

function createLight(state: LightState): Light {
  switch (state.type) {
    case 'point':
      return new PointLight();
    case 'spot': {
      const spot = new SpotLight();
      spot.penumbra = 0.3;
      return spot;
    }
    case 'area':
      if (!rectAreaReady) {
        RectAreaLightUniformsLib.init();
        rectAreaReady = true;
      }
      return new RectAreaLight();
  }
}

function createHelper(light: Light): Entry['helper'] {
  if (light instanceof PointLight) return new PointLightHelper(light, HELPER_SIZE);
  if (light instanceof SpotLight) return new SpotLightHelper(light);
  return new RectAreaLightHelper(light as RectAreaLight);
}

/** Luces puntuales, focos y de área editables, con gizmo de traslación. */
export class ExtraLights {
  private readonly entries = new Map<string, Entry>();
  private readonly gizmo: TransformControls;
  private selectedId: string | null = null;

  constructor(
    private readonly scene: Scene,
    camera: Camera,
    dom: HTMLElement,
    orbit: OrbitControls,
    private readonly onMoved: (id: string, position: Vec3) => void,
  ) {
    this.gizmo = new TransformControls(camera, dom);
    this.gizmo.addEventListener('dragging-changed', (event) => {
      orbit.enabled = !(event as unknown as { value: boolean }).value;
    });
    this.gizmo.addEventListener('objectChange', () => {
      const object = this.gizmo.object;
      if (this.selectedId && object) this.onMoved(this.selectedId, object.position.toArray());
    });
    scene.add(this.gizmo.getHelper());
  }

  setCamera(camera: Camera): void {
    this.gizmo.camera = camera;
  }

  /** Arrastrando el gizmo: no seleccionar ni orbitar. */
  get dragging(): boolean {
    return this.gizmo.dragging;
  }

  sync(states: LightState[]): void {
    const ids = new Set(states.map((s) => s.id));
    for (const [id, entry] of this.entries) {
      if (!ids.has(id)) this.remove(id, entry);
    }
    for (const state of states) {
      let entry = this.entries.get(state.id);
      if (!entry || this.typeOf(entry.light) !== state.type) {
        if (entry) this.remove(state.id, entry);
        const light = createLight(state);
        entry = { light, helper: createHelper(light) };
        this.scene.add(light);
        if (light instanceof SpotLight) this.scene.add(light.target);
        if (light instanceof RectAreaLight) light.add(entry.helper);
        else this.scene.add(entry.helper);
        this.entries.set(state.id, entry);
      }
      this.applyTo(entry, state);
    }
    if (this.selectedId && !ids.has(this.selectedId)) this.select(null);
  }

  select(id: string | null): void {
    this.selectedId = id;
    const entry = id ? this.entries.get(id) : undefined;
    if (entry) this.gizmo.attach(entry.light);
    else this.gizmo.detach();
  }

  update(): void {
    for (const { helper } of this.entries.values()) helper.update?.();
  }

  private typeOf(light: Light): LightType {
    if (light instanceof PointLight) return 'point';
    if (light instanceof SpotLight) return 'spot';
    return 'area';
  }

  private applyTo(entry: Entry, state: LightState): void {
    const { light } = entry;
    light.position.set(...state.position);
    light.color.set(state.color);
    light.intensity = state.intensity;
    const target = new Vector3(...(state.target ?? [state.position[0], 0, state.position[2]]));
    if (light instanceof PointLight) {
      light.distance = state.distance ?? 0;
    } else if (light instanceof SpotLight) {
      light.distance = state.distance ?? 0;
      light.angle = (state.angleDeg ?? 30) * DEG;
      light.target.position.copy(target);
      light.target.updateMatrixWorld();
    } else if (light instanceof RectAreaLight) {
      light.width = state.width ?? 4;
      light.height = state.height ?? 2;
      light.lookAt(target);
    }
  }

  private remove(id: string, entry: Entry): void {
    if (this.selectedId === id) this.select(null);
    entry.helper.removeFromParent();
    entry.helper.dispose?.();
    entry.light.removeFromParent();
    if (entry.light instanceof SpotLight) entry.light.target.removeFromParent();
    entry.light.dispose();
    this.entries.delete(id);
  }
}
