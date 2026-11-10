/**
 * GLB del pipeline + una cámara glTF por vista + luces (`KHR_lights_punctual`) de una vista.
 * Se parchea solo el JSON: el chunk binario (y por tanto compresión, extras y _OBJECT_ID) no
 * se toca.
 */
import { Color, Matrix4, Quaternion, Vector3 } from 'three';
import { daylightFactor, kelvinToRgb, sunDirection } from '../scene/sun';
import type { Vec3 } from '../state';
import type { SceneView } from '../views/viewState';
import { encodeGlb, type GltfJson, parseGlb } from './glb';

export const LIGHTS_EXTENSION = 'KHR_lights_punctual';
const DEG = Math.PI / 180;
/** Distancia (m) a la que se coloca el nodo del sol; solo importa su orientación. */
const SUN_NODE_DISTANCE = 100;
/** Penumbra fija de los focos del visor (SpotLight.penumbra). */
export const SPOT_PENUMBRA = 0.3;

type Quat = [number, number, number, number];

/**
 * Rotación de un objeto que mira de `position` a `target` (−Z adelante, +Y arriba), con la
 * misma matemática que `Object3D.lookAt` de Three.js para cámaras: el giro resultante coincide
 * con lo que muestra el visor, también en vistas casi verticales.
 */
export function lookAtQuaternion(position: Vec3, target: Vec3, up: Vec3 = [0, 1, 0]): Quat {
  const m = new Matrix4().lookAt(
    new Vector3(...position),
    new Vector3(...target),
    new Vector3(...up),
  );
  return new Quaternion().setFromRotationMatrix(m).toArray();
}
function linearColor(hex: string): Vec3 {
  return new Color(hex).toArray() as Vec3;
}

export interface ExportResult {
  glb: ArrayBuffer;
  cameras: number;
  lights: number;
  /** Elementos que glTF no puede representar (p. ej. luces de área). */
  skipped: string[];
}

export function addCamerasAndLights(
  source: ArrayBuffer,
  views: SceneView[],
  lightsFrom: SceneView | null,
): ExportResult {
  const { json, bin } = parseGlb(source);
  const doc: GltfJson = structuredClone(json);
  doc.nodes ??= [];
  doc.cameras ??= [];
  doc.scenes ??= [{ nodes: [] }];
  const scene = doc.scenes[doc.scene ?? 0] ?? doc.scenes[0]!;
  scene.nodes ??= [];
  const children: number[] = [];
  const addNode = (node: Record<string, unknown>): void => {
    doc.nodes!.push(node);
    children.push(doc.nodes!.length - 1);
  };

  for (const view of views) {
    const c = view.camera;
    const aspect = view.render.width / view.render.height;
    const camera =
      c.type === 'perspective'
        ? {
            name: view.name,
            type: 'perspective',
            perspective: { yfov: c.fovDeg * DEG, aspectRatio: aspect, znear: c.near, zfar: c.far },
          }
        : {
            name: view.name,
            type: 'orthographic',
            orthographic: {
              xmag: c.orthoSize * aspect,
              ymag: c.orthoSize,
              znear: Math.max(c.near, 0),
              zfar: Math.max(c.far, Math.max(c.near, 0) + 1),
            },
          };
    doc.cameras.push(camera);
    addNode({
      name: `Vista: ${view.name}`,
      camera: doc.cameras.length - 1,
      translation: c.position,
      rotation: lookAtQuaternion(c.position, c.target, c.up),
      extras: { extrude_view_id: view.id, render: view.render },
    });
  }

  const skipped: string[] = [];
  const lights: Record<string, unknown>[] = [];
  if (lightsFrom) {
    const sun = lightsFrom.sun;
    const toSun = sunDirection(sun, sun.northRotationDeg);
    const intensity = sun.intensity * daylightFactor(sun.elevationDeg);
    if (intensity > 0) {
      lights.push({
        name: 'Sol',
        type: 'directional',
        color: kelvinToRgb(sun.colorTempK),
        intensity,
      });
      const position = toSun.clone().multiplyScalar(SUN_NODE_DISTANCE).toArray();
      addNode({
        name: 'Luz: Sol',
        translation: position,
        rotation: lookAtQuaternion(position, [0, 0, 0]),
        extensions: { [LIGHTS_EXTENSION]: { light: lights.length - 1 } },
        extras: {
          azimuthDeg: sun.azimuthDeg,
          elevationDeg: sun.elevationDeg,
          isoDate: sun.isoDate,
        },
      });
    }
    lightsFrom.lights.forEach((light, i) => {
      const name = `Luz ${i + 1} (${light.type})`;
      if (light.type === 'area') {
        skipped.push(`${name}: KHR_lights_punctual no admite luces de área`);
        return;
      }
      const entry: Record<string, unknown> = {
        name,
        type: light.type,
        color: linearColor(light.color),
        intensity: light.intensity,
      };
      if (light.distance) entry['range'] = light.distance;
      if (light.type === 'spot') {
        const outer = (light.angleDeg ?? 30) * DEG;
        entry['spot'] = { innerConeAngle: outer * (1 - SPOT_PENUMBRA), outerConeAngle: outer };
      }
      lights.push(entry);
      const target = light.target ?? [light.position[0], 0, light.position[2]];
      addNode({
        name: `Luz: ${name}`,
        translation: light.position,
        ...(light.type === 'spot' ? { rotation: lookAtQuaternion(light.position, target) } : {}),
        extensions: { [LIGHTS_EXTENSION]: { light: lights.length - 1 } },
      });
    });
  }

  if (lights.length > 0) {
    doc.extensions = { ...doc.extensions, [LIGHTS_EXTENSION]: { lights } };
    doc.extensionsUsed = [...new Set([...(doc.extensionsUsed ?? []), LIGHTS_EXTENSION])];
  }
  if (doc.cameras.length === 0) delete doc.cameras;
  if (children.length > 0) {
    doc.nodes.push({ name: 'Extrude: vistas y luces', children });
    scene.nodes.push(doc.nodes.length - 1);
  }
  return {
    glb: encodeGlb({ json: doc, bin }),
    cameras: views.length,
    lights: lights.length,
    skipped,
  };
}
