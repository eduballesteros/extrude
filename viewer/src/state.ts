/**
 * Estado del visor: todo lo que define una vista es serializable a JSON (Fase 5: SceneView).
 * Las coordenadas están en el sistema de la escena cargada (metros, Y-up, recentrado).
 */

export type Vec3 = [number, number, number];

export type SunMode = 'datetime' | 'manual';
export type ToneMappingType = 'aces' | 'agx' | 'neutral' | 'none';
export type ShadowType = 'pcf' | 'vsm';
export type ShadowFit = 'auto' | 'scene' | 'view';
export type BackgroundMode = 'sky' | 'hdri' | 'color';
export type LightType = 'point' | 'spot' | 'area';
export type CameraType = 'perspective' | 'orthographic';

export interface SunState {
  mode: SunMode;
  /** Fecha y hora local con desfase, p. ej. "2024-06-21T10:00:00+02:00". */
  isoDate: string;
  lat: number;
  lon: number;
  /** Azimut desde el norte, en sentido horario (0 = N, 90 = E). */
  azimuthDeg: number;
  elevationDeg: number;
  /** Ángulo horario del norte real respecto al +Y del dibujo (−Z de la escena). */
  northRotationDeg: number;
  intensity: number;
  colorTempK: number;
}

export interface AmbientState {
  intensity: number;
  skyColor: string;
  groundColor: string;
  background: BackgroundMode;
  backgroundColor: string;
  /** Nombre del archivo HDRI cargado (el contenido no se serializa). */
  hdri?: string;
  hdriRotationDeg: number;
  hdriIntensity: number;
}

export interface ShadowState {
  enabled: boolean;
  mapSize: number;
  bias: number;
  normalBias: number;
  type: ShadowType;
  /** Radio de suavizado del borde (PCF y VSM). */
  radius: number;
  fit: ShadowFit;
}

export interface LightState {
  id: string;
  type: LightType;
  position: Vec3;
  target?: Vec3;
  color: string;
  intensity: number;
  distance?: number;
  angleDeg?: number;
  width?: number;
  height?: number;
}

export interface LayerState {
  visible: boolean;
  colorOverride?: string;
}

export interface GroundState {
  /** Plano bajo el modelo que recibe sombras (útil si el DWG no trae terreno). */
  enabled: boolean;
  color: string;
}

export interface ToneMappingState {
  type: ToneMappingType;
  exposure: number;
}

export interface CameraState {
  type: CameraType;
  position: Vec3;
  target: Vec3;
  up: Vec3;
  fovDeg: number;
  orthoSize: number;
  near: number;
  far: number;
}

export interface ViewerState {
  camera: CameraState;
  sun: SunState;
  ambient: AmbientState;
  shadows: ShadowState;
  lights: LightState[];
  layers: Record<string, LayerState>;
  ground: GroundState;
  tonemapping: ToneMappingState;
}

export function defaultState(): ViewerState {
  return {
    camera: {
      type: 'perspective',
      position: [80, 60, 80],
      target: [0, 0, 0],
      up: [0, 1, 0],
      fovDeg: 50,
      orthoSize: 100,
      near: 0.1,
      far: 5000,
    },
    sun: {
      mode: 'datetime',
      isoDate: '2024-06-21T12:00:00+02:00',
      lat: 40.4168,
      lon: -3.7038,
      azimuthDeg: 180,
      elevationDeg: 45,
      northRotationDeg: 0,
      intensity: 3,
      colorTempK: 5800,
    },
    ambient: {
      intensity: 0.6,
      skyColor: '#ffffff',
      groundColor: '#8d8d7a',
      background: 'sky',
      backgroundColor: '#bfd1e5',
      hdriRotationDeg: 0,
      hdriIntensity: 1,
    },
    shadows: {
      enabled: true,
      mapSize: 4096,
      bias: -0.0002,
      normalBias: 0.02,
      type: 'pcf',
      radius: 3,
      fit: 'auto',
    },
    lights: [],
    layers: {},
    ground: { enabled: true, color: '#c9c6bb' },
    tonemapping: { type: 'agx', exposure: 1 },
  };
}
