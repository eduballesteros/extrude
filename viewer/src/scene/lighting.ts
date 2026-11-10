import {
  ACESFilmicToneMapping,
  AgXToneMapping,
  Box3,
  Color,
  DirectionalLight,
  EquirectangularReflectionMapping,
  HemisphereLight,
  type Mesh,
  NeutralToneMapping,
  NoToneMapping,
  PCFShadowMap,
  PMREMGenerator,
  Scene,
  type Texture,
  type ToneMapping,
  Vector3,
  VSMShadowMap,
  type WebGLRenderer,
  type WebGLRenderTarget,
} from 'three';
import { Sky } from 'three/examples/jsm/objects/Sky.js';
import type { AmbientState, ShadowState, SunState, ToneMappingState } from '../state';
import { fitShadow, shouldFitToView, viewBox } from './shadows';
import { daylightFactor, kelvinToRgb, sunAngles, sunDirection } from './sun';

const DEG = Math.PI / 180;
const SKY_SCALE = 1e5;
/** Regenerar el entorno del cielo cuesta unos ms: como mucho cada este intervalo. */
const SKY_ENV_INTERVAL_MS = 200;
/**
 * El entorno generado con el cielo procedural es muy intenso (radiancia física del cielo);
 * se escala para que, con "Luz ambiente" = 0,6 (valor por defecto), el contraste entre caras al sol y a la sombra se
 * parezca al de una foto exterior (medido en el visor sobre la manzana de ejemplo).
 */
const SKY_ENV_SCALE = 0.5;

const TONE_MAPPINGS: Record<ToneMappingState['type'], ToneMapping> = {
  aces: ACESFilmicToneMapping,
  agx: AgXToneMapping,
  neutral: NeutralToneMapping,
  none: NoToneMapping,
};

function makeSky(scale: number): Sky {
  const sky = new Sky();
  sky.scale.setScalar(scale);
  sky.frustumCulled = false;
  const u = sky.material.uniforms;
  u['turbidity']!.value = 6;
  u['rayleigh']!.value = 1.5;
  u['mieCoefficient']!.value = 0.005;
  u['mieDirectionalG']!.value = 0.8;
  if (u['cloudCoverage']) u['cloudCoverage'].value = 0.15;
  return sky;
}

/** Sol, luz ambiental hemisférica, cielo/HDRI, sombras y tone mapping. */
export class SceneLighting {
  readonly sun = new DirectionalLight(0xffffff, 3);
  readonly hemi = new HemisphereLight(0xffffff, 0x8d8d7a, 0.6);
  readonly toSun = new Vector3(0, 1, 0);
  private readonly sky = makeSky(SKY_SCALE);
  private readonly envScene = new Scene();
  private readonly envSky = makeSky(10);
  private readonly pmrem: PMREMGenerator;
  private skyEnv: WebGLRenderTarget | null = null;
  private hdriEnv: WebGLRenderTarget | null = null;
  private hdriTexture: Texture | null = null;
  private skyDirty = true;
  private lastSkyUpdate = 0;
  private background: AmbientState['background'] = 'sky';
  private sceneBox = new Box3();

  constructor(
    private readonly scene: Scene,
    private readonly renderer: WebGLRenderer,
  ) {
    this.pmrem = new PMREMGenerator(renderer);
    this.sun.castShadow = true;
    scene.add(this.sun, this.sun.target, this.hemi, this.sky);
    this.envScene.add(this.envSky);
  }

  /** Calcula la posición solar; con mode 'datetime' escribe azimut/elevación en `state`. */
  applySun(state: SunState): void {
    if (state.mode === 'datetime') {
      const angles = sunAngles(new Date(state.isoDate), state.lat, state.lon);
      state.azimuthDeg = angles.azimuthDeg;
      state.elevationDeg = angles.elevationDeg;
    }
    this.toSun.copy(sunDirection(state, state.northRotationDeg));
    const daylight = daylightFactor(state.elevationDeg);
    this.sun.intensity = state.intensity * daylight;
    this.sun.visible = daylight > 0;
    this.sun.color.setRGB(...kelvinToRgb(state.colorTempK));
    // El cielo se ilumina aunque el sol esté justo bajo el horizonte
    const skyDir = this.toSun.clone();
    if (skyDir.y < -0.05) skyDir.y = -0.05;
    for (const sky of [this.sky, this.envSky])
      (sky.material.uniforms['sunPosition']!.value as Vector3).copy(skyDir);
    this.skyDirty = true;
  }

  applyAmbient(state: AmbientState): void {
    this.hemi.color.set(state.skyColor);
    this.hemi.groundColor.set(state.groundColor);
    this.background = state.background === 'hdri' && !this.hdriTexture ? 'sky' : state.background;
    this.sky.visible = this.background === 'sky';
    // Con cielo o HDRI la luz ambiente sale del entorno; la hemisférica solo con fondo de color
    this.hemi.visible = this.background === 'color';
    this.hemi.intensity = state.intensity;
    this.scene.environmentIntensity =
      state.intensity * (this.background === 'hdri' ? state.hdriIntensity : SKY_ENV_SCALE);
    this.scene.backgroundIntensity = state.hdriIntensity;
    this.scene.environmentRotation.set(0, state.hdriRotationDeg * DEG, 0);
    this.scene.backgroundRotation.set(0, state.hdriRotationDeg * DEG, 0);
    if (this.background === 'hdri' && this.hdriTexture && this.hdriEnv) {
      this.scene.background = this.hdriTexture;
      this.scene.environment = this.hdriEnv.texture;
    } else {
      const color = this.background === 'color';
      this.scene.background = color ? new Color(state.backgroundColor) : null;
      this.scene.environment = color ? null : (this.skyEnv?.texture ?? null);
      this.skyDirty = !color;
    }
  }

  setHdri(texture: Texture): void {
    texture.mapping = EquirectangularReflectionMapping;
    this.hdriTexture?.dispose();
    this.hdriEnv?.dispose();
    this.hdriTexture = texture;
    this.hdriEnv = this.pmrem.fromEquirectangular(texture);
  }

  applyShadows(state: ShadowState): void {
    const shadow = this.sun.shadow;
    this.sun.castShadow = state.enabled;
    const type = state.type === 'vsm' ? VSMShadowMap : PCFShadowMap;
    const map = this.renderer.shadowMap;
    // Activar/desactivar o cambiar el filtro exige recompilar los shaders de todos los materiales
    if (map.type !== type || map.enabled !== state.enabled) {
      map.type = type;
      map.enabled = state.enabled;
      this.scene.traverse((o) => {
        const mesh = o as Mesh;
        if (!mesh.isMesh) return;
        const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
        for (const m of materials) m.needsUpdate = true;
      });
    }
    if (shadow.mapSize.x !== state.mapSize) {
      shadow.mapSize.set(state.mapSize, state.mapSize);
      shadow.map?.dispose();
      shadow.map = null;
    }
    shadow.bias = state.bias;
    shadow.normalBias = state.normalBias;
    shadow.radius = state.radius;
    shadow.blurSamples = 16;
  }

  setSceneBox(box: Box3): void {
    this.sceneBox = box.clone();
  }

  /** Ajusta la cámara de sombras a la escena o a la zona visible (por fotograma si es vista). */
  fitShadows(state: ShadowState, cameraTarget: Vector3, cameraDistance: number): void {
    if (this.sceneBox.isEmpty()) return;
    const toView = shouldFitToView(state.fit, this.sceneBox);
    const box = toView ? viewBox(this.sceneBox, cameraTarget, cameraDistance) : this.sceneBox;
    const f = fitShadow(box, this.toSun, toView ? state.mapSize : undefined);
    const camera = this.sun.shadow.camera;
    this.sun.position.copy(f.position);
    this.sun.target.position.copy(f.target);
    camera.up.copy(f.up);
    camera.left = f.left;
    camera.right = f.right;
    camera.top = f.top;
    camera.bottom = f.bottom;
    camera.near = f.near;
    camera.far = f.far;
    camera.updateProjectionMatrix();
    this.sun.target.updateMatrixWorld();
  }

  applyToneMapping(state: ToneMappingState): void {
    this.renderer.toneMapping = TONE_MAPPINGS[state.type];
    this.renderer.toneMappingExposure = state.exposure;
  }

  /** Llamar por fotograma: regenera el entorno del cielo si cambió el sol. */
  update(now: number): void {
    if (!this.skyDirty || this.background !== 'sky') return;
    if (now - this.lastSkyUpdate < SKY_ENV_INTERVAL_MS) return;
    this.skyDirty = false;
    this.lastSkyUpdate = now;
    const previous = this.skyEnv;
    this.skyEnv = this.pmrem.fromScene(this.envScene, 0, 0.1, 100);
    this.scene.environment = this.skyEnv.texture;
    previous?.dispose();
  }
}
