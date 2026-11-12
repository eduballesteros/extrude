import {
  Box3,
  type Camera,
  MathUtils,
  type Object3D,
  OrthographicCamera,
  PerspectiveCamera,
  Sphere,
  Vector3,
} from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { PointerLockControls } from 'three/examples/jsm/controls/PointerLockControls.js';
import type { CameraState, CameraType, Vec3 } from '../state';

const FIT_MARGIN = 1.2;
/** far/near máximo para no perder precisión de profundidad (24 bits). */
const MAX_DEPTH_RATIO = 50_000;
const MIN_NEAR = 0.05;
const FOCUS_DURATION_S = 0.5;
/** Velocidad de vuelo: fracción de la diagonal de la escena por segundo. */
const FLY_SPEED_FRACTION = 0.05;
const FLY_FAST_FACTOR = 4;

const FLY_KEYS: Record<string, Vec3> = {
  KeyW: [0, 0, 1],
  KeyS: [0, 0, -1],
  KeyA: [-1, 0, 0],
  KeyD: [1, 0, 0],
  KeyE: [0, 1, 0],
  KeyQ: [0, -1, 0],
};

interface Tween {
  fromTarget: Vector3;
  toTarget: Vector3;
  fromPosition: Vector3;
  toPosition: Vector3;
  elapsed: number;
}

/** Vistas estándar. Los puntos cardinales son los del dibujo (+Y DWG = norte = −Z escena). */
export type ViewPreset = 'top' | 'north' | 'south' | 'east' | 'west' | 'iso';

/** Dirección desde el objetivo hacia la cámara para cada vista estándar. */
export const PRESET_DIRECTIONS: Record<ViewPreset, Vec3> = {
  // Planta: casi vertical, con un mínimo desplazamiento al sur para que el norte quede arriba
  top: [0, 1, 1e-4],
  north: [0, 0, -1],
  south: [0, 0, 1],
  east: [1, 0, 0],
  west: [-1, 0, 0],
  iso: [1, 0.8, 1],
};

export function boundsOf(object: Object3D): Box3 {
  object.updateMatrixWorld(true);
  return new Box3().setFromObject(object);
}

/** Cámaras perspectiva/ortográfica, órbita, vuelo (WASD) y encuadre automático. */
export class Navigation {
  readonly perspective = new PerspectiveCamera(50, 1, 0.1, 5000);
  readonly orthographic = new OrthographicCamera(-1, 1, 1, -1, 0.1, 5000);
  readonly orbit: OrbitControls;
  readonly fly: PointerLockControls;
  private type: CameraType = 'perspective';
  private orthoSize = 100;
  private aspect = 1;
  private sceneBox = new Box3();
  private tween: Tween | null = null;
  private readonly pressed = new Set<string>();
  private fast = false;

  constructor(
    dom: HTMLElement,
    private readonly onCameraChange: (camera: Camera) => void,
    onFlyError: () => void,
  ) {
    this.orbit = new OrbitControls(this.perspective, dom);
    this.orbit.enableDamping = true;
    this.fly = new PointerLockControls(this.perspective, dom);
    // La órbita se desactiva solo si el bloqueo del puntero llega a concederse
    this.fly.addEventListener('lock', () => (this.orbit.enabled = false));
    this.fly.addEventListener('unlock', () => this.endFly());
    document.addEventListener('pointerlockerror', () => onFlyError());
    window.addEventListener('keydown', (e) => this.onKey(e, true));
    window.addEventListener('keyup', (e) => this.onKey(e, false));
  }

  get camera(): PerspectiveCamera | OrthographicCamera {
    return this.type === 'perspective' ? this.perspective : this.orthographic;
  }

  get cameraType(): CameraType {
    return this.type;
  }

  get flying(): boolean {
    return this.fly.isLocked;
  }

  get distance(): number {
    return this.camera.position.distanceTo(this.orbit.target);
  }

  resize(width: number, height: number): void {
    this.aspect = width / Math.max(height, 1);
    this.perspective.aspect = this.aspect;
    this.perspective.updateProjectionMatrix();
    this.updateOrtho();
  }

  setSceneBox(box: Box3): void {
    this.sceneBox = box.clone();
  }

  /** Encuadra `box` en vista 3/4. */
  frame(box: Box3 = this.sceneBox): void {
    if (box.isEmpty()) return;
    const sphere = box.getBoundingSphere(new Sphere());
    const fov = this.perspective.fov * MathUtils.DEG2RAD;
    const distance = (sphere.radius * FIT_MARGIN) / Math.sin(fov / 2);
    const direction = new Vector3(1, 0.8, 1).normalize();
    this.tween = null;
    this.orbit.target.copy(sphere.center);
    this.camera.position.copy(sphere.center).addScaledVector(direction, distance);
    this.orthoSize = sphere.radius * FIT_MARGIN;
    this.orthographic.zoom = 1;
    this.syncCameras();
    this.updateOrtho();
    this.orbit.update();
  }

  setType(type: CameraType): void {
    if (type === this.type) return;
    const from = this.camera;
    if (type === 'orthographic') {
      // Mismo encuadre aproximado a la distancia del objetivo
      const fov = this.perspective.fov * MathUtils.DEG2RAD;
      this.orthoSize = this.distance * Math.tan(fov / 2);
      this.orthographic.zoom = 1;
    }
    this.type = type;
    this.camera.position.copy(from.position);
    this.camera.up.copy(from.up);
    this.orbit.object = this.camera;
    this.updateOrtho();
    this.orbit.update();
    this.onCameraChange(this.camera);
  }

  setFov(fovDeg: number): void {
    this.perspective.fov = fovDeg;
    this.perspective.updateProjectionMatrix();
  }

  /** Lleva el objetivo de la órbita a `point` (animado), conservando el desplazamiento. */
  focus(point: Vector3): void {
    const offset = this.camera.position.clone().sub(this.orbit.target);
    this.tween = {
      fromTarget: this.orbit.target.clone(),
      toTarget: point.clone(),
      fromPosition: this.camera.position.clone(),
      toPosition: point.clone().add(offset),
      elapsed: 0,
    };
  }

  /** Anima la cámara a una vista estándar conservando objetivo y distancia. */
  viewFrom(preset: ViewPreset): void {
    const direction = new Vector3(...PRESET_DIRECTIONS[preset]).normalize();
    const target = this.orbit.target.clone();
    this.animateTo(target.clone().addScaledVector(direction, this.distance), target);
  }

  /**
   * Cámara final (sin animar) para una vista estándar: dirección del preset y, si se da `box`,
   * encuadre de esa caja; si no, el objetivo y la distancia actuales. Sirve para guardar vistas
   * con exactamente lo pedido, sin depender de que termine una animación.
   */
  presetCamera(preset: ViewPreset, box?: Box3): CameraState {
    const direction = new Vector3(...PRESET_DIRECTIONS[preset]).normalize();
    const current = this.snapshot();
    let target = this.orbit.target.clone();
    let distance = this.distance;
    let orthoSize = current.orthoSize;
    if (box && !box.isEmpty()) {
      const sphere = box.getBoundingSphere(new Sphere());
      target = sphere.center.clone();
      distance =
        (sphere.radius * FIT_MARGIN) / Math.sin((this.perspective.fov * MathUtils.DEG2RAD) / 2);
      orthoSize = sphere.radius * FIT_MARGIN;
    }
    const position = target.clone().addScaledVector(direction, distance);
    return { ...current, position: position.toArray(), target: target.toArray(), orthoSize };
  }

  /** Igual que `focus`, pero hacia una posición y objetivo concretos (restaurar vistas). */
  animateTo(position: Vector3, target: Vector3): void {
    this.tween = {
      fromTarget: this.orbit.target.clone(),
      toTarget: target.clone(),
      fromPosition: this.camera.position.clone(),
      toPosition: position.clone(),
      elapsed: 0,
    };
  }

  startFly(): void {
    if (this.type !== 'perspective') this.setType('perspective');
    this.fly.lock();
  }

  update(dt: number): void {
    if (this.tween) this.stepTween(dt);
    if (this.fly.isLocked) this.stepFly(dt);
    else this.orbit.update();
    this.updateClipping();
  }

  snapshot(): CameraState {
    const fov = this.perspective.fov;
    return {
      type: this.type,
      position: this.camera.position.toArray(),
      target: this.orbit.target.toArray(),
      up: this.camera.up.toArray(),
      fovDeg: fov,
      orthoSize: this.orthoSize / this.orthographic.zoom,
      near: this.camera.near,
      far: this.camera.far,
    };
  }

  restore(state: CameraState, animate: boolean): void {
    this.setType(state.type);
    this.setFov(state.fovDeg);
    this.orthoSize = state.orthoSize;
    this.orthographic.zoom = 1;
    this.camera.up.set(...state.up);
    this.updateOrtho();
    const position = new Vector3(...state.position);
    const target = new Vector3(...state.target);
    if (animate) {
      this.animateTo(position, target);
    } else {
      this.tween = null;
      this.camera.position.copy(position);
      this.orbit.target.copy(target);
      this.orbit.update();
    }
  }

  private stepTween(dt: number): void {
    const tween = this.tween!;
    tween.elapsed += dt;
    const t = Math.min(tween.elapsed / FOCUS_DURATION_S, 1);
    const k = t * t * (3 - 2 * t);
    this.orbit.target.lerpVectors(tween.fromTarget, tween.toTarget, k);
    this.camera.position.lerpVectors(tween.fromPosition, tween.toPosition, k);
    if (t >= 1) this.tween = null;
  }

  private stepFly(dt: number): void {
    const move = new Vector3();
    for (const code of this.pressed) {
      const dir = FLY_KEYS[code];
      if (dir) move.add(new Vector3(...dir));
    }
    if (move.lengthSq() === 0) return;
    const diagonal = this.sceneBox.isEmpty() ? 100 : this.sceneBox.getSize(new Vector3()).length();
    const speed = diagonal * FLY_SPEED_FRACTION * (this.fast ? FLY_FAST_FACTOR : 1) * dt;
    move.normalize().multiplyScalar(speed);
    this.fly.moveRight(move.x);
    this.fly.moveForward(move.z);
    this.camera.position.y += move.y;
  }

  private endFly(): void {
    this.pressed.clear();
    const forward = new Vector3();
    this.camera.getWorldDirection(forward);
    this.orbit.target
      .copy(this.camera.position)
      .addScaledVector(forward, Math.max(this.distance, 10));
    this.orbit.enabled = true;
  }

  private onKey(event: KeyboardEvent, down: boolean): void {
    this.fast = event.shiftKey;
    if (!this.fly.isLocked || !(event.code in FLY_KEYS)) return;
    if (down) this.pressed.add(event.code);
    else this.pressed.delete(event.code);
  }

  private syncCameras(): void {
    const other = this.type === 'perspective' ? this.orthographic : this.perspective;
    other.position.copy(this.camera.position);
    other.quaternion.copy(this.camera.quaternion);
  }

  private updateOrtho(): void {
    const half = this.orthoSize;
    this.orthographic.left = -half * this.aspect;
    this.orthographic.right = half * this.aspect;
    this.orthographic.top = half;
    this.orthographic.bottom = -half;
    this.orthographic.updateProjectionMatrix();
  }

  /** near/far a partir de la distancia de la cámara a la escena. */
  private updateClipping(): void {
    if (this.sceneBox.isEmpty()) return;
    const camera = this.camera;
    const sphere = this.sceneBox.getBoundingSphere(new Sphere());
    const toCenter = camera.position.distanceTo(sphere.center);
    const far = toCenter + sphere.radius * 2;
    const near =
      camera instanceof OrthographicCamera
        ? -far
        : Math.max(far / MAX_DEPTH_RATIO, MIN_NEAR, (toCenter - sphere.radius) * 0.01);
    if (Math.abs(camera.far - far) / far > 0.01 || Math.abs(camera.near - near) > 1e-6) {
      camera.far = far;
      camera.near = near;
      camera.updateProjectionMatrix();
    }
  }
}
