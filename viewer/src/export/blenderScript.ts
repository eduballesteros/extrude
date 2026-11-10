/**
 * Genera `blender_setup.py`: importa el GLB y crea, por vista, cámara, sol, luces, mundo y
 * visibilidad de capas, con un helper para renderizar todas las vistas.
 *
 * Las conversiones se calculan aquí (testeables) y el script solo las aplica:
 * - Ejes: escena (Y-up) → Blender (Z-up): (x, y, z) → (x, −z, y), como el importador glTF.
 * - Lente: sensor vertical de 24 mm; lens = 12 / tan(fov_v / 2).
 * - Potencias (aproximadas): se conservan las proporciones entre luces del visor con una escala
 *   global elegida para que el sol del visor (intensidad 3) dé Strength 3 en Blender:
 *     sol:      Strength [W/m²] = intensidad (lux de Three)
 *     puntual:  W = candelas × 4π
 *     foco:     W = candelas × 4π (Blender define la potencia del foco como si fuese omni)
 *     área:     W = luminancia (nits) × ancho × alto × π
 * Los datos van codificados en Base64: nombres de vistas y capas son texto del usuario/DWG y
 * no pueden romper el código Python.
 */
import { Color, Quaternion, Vector3 } from 'three';
import { daylightFactor, kelvinToRgb, sunDirection } from '../scene/sun';
import type { ToneMappingType, Vec3 } from '../state';
import { hiddenLayers, type ModelInfo, type SceneView, sceneToBlender } from '../views/viewState';
import { lookAtQuaternion, SPOT_PENUMBRA } from './gltfWithLights';

export const SENSOR_HEIGHT_MM = 24;
const DEG = Math.PI / 180;
/** Diámetro aparente del sol (°): sombras con la penumbra real. */
const SUN_ANGLE_DEG = 0.53;
/** Escala del mundo con cielo procedural: la misma que el visor aplica al entorno. */
const SKY_WORLD_SCALE = 0.5;
const SKY_COLOR = '#bfd1e5';

const VIEW_TRANSFORMS: Record<ToneMappingType, string> = {
  agx: 'AgX',
  neutral: 'Khronos PBR Neutral',
  aces: 'Filmic', // Blender no trae ACES por defecto: Filmic es lo más parecido
  none: 'Standard',
};

/** Escena (Y-up) → Blender (Z-up): rotación de +90° alrededor de X. */
const SCENE_TO_BLENDER = new Quaternion().setFromAxisAngle(new Vector3(1, 0, 0), Math.PI / 2);

export type QuatWXYZ = [number, number, number, number];

/**
 * Orientación en Blender (w, x, y, z, como mathutils) de un objeto que mira de position a
 * 	arget en la escena, con la MISMA matemática que Three.js (lookAt + up). Así el giro de la
 * cámara coincide con el visor también en vistas casi verticales (plantas).
 */
export function blenderRotation(position: Vec3, target: Vec3, up: Vec3 = [0, 1, 0]): QuatWXYZ {
  const scene = new Quaternion(...lookAtQuaternion(position, target, up));
  const q = SCENE_TO_BLENDER.clone().multiply(scene);
  return [q.w, q.x, q.y, q.z];
}

export function lensFromFov(fovDeg: number): number {
  return SENSOR_HEIGHT_MM / 2 / Math.tan((fovDeg * DEG) / 2);
}

function linear(hex: string): Vec3 {
  return new Color(hex).toArray() as Vec3;
}

export interface BlenderLight {
  type: 'POINT' | 'SPOT' | 'AREA';
  name: string;
  position: Vec3;
  target: Vec3;
  rotation: QuatWXYZ;
  color: Vec3;
  power_w: number;
  spot_size_deg?: number;
  spot_blend?: number;
  size_x?: number;
  size_y?: number;
}

export interface BlenderView {
  name: string;
  camera: {
    type: 'PERSP' | 'ORTHO';
    position: Vec3;
    target: Vec3;
    rotation: QuatWXYZ;
    lens_mm: number;
    sensor_height_mm: number;
    fov_deg: number;
    ortho_scale: number;
    clip_start: number;
    clip_end: number;
  };
  render: { width: number; height: number };
  sun: {
    to_sun: Vec3;
    strength: number;
    color: Vec3;
    angle_deg: number;
    azimuth_deg: number;
    elevation_deg: number;
  };
  lights: BlenderLight[];
  world: {
    mode: 'sky' | 'hdri' | 'color';
    color: Vec3;
    strength: number;
    hdri: string | null;
    hdri_rotation_deg: number;
  };
  hidden_layers: string[];
  view_transform: string;
  exposure_stops: number;
}

export interface BlenderData {
  generator: string;
  model: { file: string; origin_offset: Vec3; crs: string | null };
  views: BlenderView[];
}

export function toBlenderView(view: SceneView): BlenderView {
  const c = view.camera;
  const sun = view.sun;
  const toSunScene = sunDirection(sun, sun.northRotationDeg).toArray();
  const ambient = view.ambient;
  const mode = ambient.background;
  const near = c.type === 'orthographic' ? Math.max(c.near, 0.01) : c.near;
  return {
    name: view.name,
    camera: {
      type: c.type === 'perspective' ? 'PERSP' : 'ORTHO',
      position: sceneToBlender(c.position),
      target: sceneToBlender(c.target),
      rotation: blenderRotation(c.position, c.target, c.up),
      lens_mm: lensFromFov(c.fovDeg),
      sensor_height_mm: SENSOR_HEIGHT_MM,
      fov_deg: c.fovDeg,
      ortho_scale: 2 * c.orthoSize,
      clip_start: near,
      clip_end: Math.max(c.far, near + 1),
    },
    render: { width: view.render.width, height: view.render.height },
    sun: {
      to_sun: sceneToBlender(toSunScene),
      strength: sun.intensity * daylightFactor(sun.elevationDeg),
      color: kelvinToRgb(sun.colorTempK),
      angle_deg: SUN_ANGLE_DEG,
      azimuth_deg: sun.azimuthDeg,
      elevation_deg: sun.elevationDeg,
    },
    lights: view.lights.map((light, i): BlenderLight => {
      const target = light.target ?? [light.position[0], 0, light.position[2]];
      const base = {
        name: `${i + 1}. ${light.type}`,
        position: sceneToBlender(light.position),
        target: sceneToBlender(target),
        rotation: blenderRotation(light.position, target),
        color: linear(light.color),
      };
      if (light.type === 'area') {
        const w = light.width ?? 4;
        const h = light.height ?? 2;
        return {
          ...base,
          type: 'AREA',
          power_w: light.intensity * w * h * Math.PI,
          size_x: w,
          size_y: h,
        };
      }
      const power = light.intensity * 4 * Math.PI;
      return light.type === 'spot'
        ? {
            ...base,
            type: 'SPOT',
            power_w: power,
            spot_size_deg: 2 * (light.angleDeg ?? 30),
            spot_blend: SPOT_PENUMBRA,
          }
        : { ...base, type: 'POINT', power_w: power };
    }),
    world: {
      mode,
      color: linear(mode === 'color' ? ambient.backgroundColor : SKY_COLOR),
      strength: ambient.intensity * (mode === 'hdri' ? ambient.hdriIntensity : SKY_WORLD_SCALE),
      hdri: ambient.hdri ?? null,
      hdri_rotation_deg: ambient.hdriRotationDeg,
    },
    hidden_layers: hiddenLayers(view),
    view_transform: VIEW_TRANSFORMS[view.tonemapping.type],
    exposure_stops: Math.log2(view.tonemapping.exposure),
  };
}

export function toBlenderData(
  views: SceneView[],
  model: ModelInfo,
  generator: string,
): BlenderData {
  return {
    generator,
    model: { file: model.file, origin_offset: model.originOffset, crs: model.crs },
    views: views.map(toBlenderView),
  };
}

function base64Utf8(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let binary = '';
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}

export function generateBlenderScript(data: BlenderData): string {
  const payload = base64Utf8(JSON.stringify(data));
  // Partir el Base64 en líneas cortas para que el script sea legible en el editor de Blender
  const lines = payload.match(/.{1,100}/g) ?? [];
  const literal = lines.map((l) => `    "${l}"`).join('\n');
  // Reemplazo con función: un $ en el texto no se interpreta como patrón de sustitución
  const generator = data.generator.replace(/[^\w .:-]/g, '');
  return PYTHON_TEMPLATE.replace('__GENERATOR__', () => generator).replace(
    '__PAYLOAD__',
    () => literal,
  );
}

const PYTHON_TEMPLATE = String.raw`# blender_setup.py — generado por __GENERATOR__
#
# Uso:
#   1. Copia este archivo junto al GLB exportado (y al HDRI si alguna vista lo usa).
#   2. En Blender: Scripting -> Abrir -> blender_setup.py -> Run Script.
#      O en consola:  blender -b -P blender_setup.py -- [--glb ruta.glb] [--render-all carpeta]
#                     [--percentage 50] [--report informe.json]
#   3. Cada vista queda como una cámara en la colección "Extrude vistas". Para renderizar una:
#        apply_view("Nombre de la vista"); bpy.ops.render.render(write_still=True)
#      o todas:  render_all("//renders")
#
# Conversiones (aproximadas, ver docs/EXPORT_FORMATS.md): ejes Y-up -> Z-up como el importador
# glTF; lente con sensor vertical de 24 mm; potencias escaladas conservando proporciones.

import base64
import json
import math
import os
import sys

import bpy
from mathutils import Quaternion, Vector

DATA = json.loads(
    base64.b64decode(
        "".join(
            (
__PAYLOAD__
            )
        )
    ).decode("utf-8")
)

SCRIPT_NAME = "blender_setup.py"
COLLECTION = "Extrude vistas"


def log(message):
    print("[extrude] " + message)


def script_dir():
    path = globals().get("__file__")
    if path and os.path.isfile(path):
        return os.path.dirname(os.path.abspath(path))
    for text in bpy.data.texts:
        if text.filepath and os.path.basename(text.filepath) == SCRIPT_NAME:
            return os.path.dirname(bpy.path.abspath(text.filepath))
    return os.getcwd()


def cli_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    options = {}
    i = 0
    while i < len(argv):
        if argv[i].startswith("--") and i + 1 < len(argv):
            options[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            i += 1
    return options


def import_model(path):
    if not os.path.isfile(path):
        raise FileNotFoundError(
            "No se encuentra el GLB '%s'. Copia el modelo junto al script o usa --glb." % path
        )
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    imported = [o for o in bpy.data.objects if o not in before]
    log("GLB importado: %d objetos" % len(imported))
    return imported


def collection(name, parent):
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        parent.children.link(col)
    return col


def place(obj, position, rotation):
    """Posición y orientación precalculadas en el visor (cuaternión w, x, y, z)."""
    obj.location = Vector(position)
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = Quaternion(rotation)


def aim(obj, position, target):
    obj.location = Vector(position)
    direction = Vector(target) - Vector(position)
    if direction.length < 1e-9:
        direction = Vector((0.0, 0.0, -1.0))
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = direction.to_track_quat("-Z", "Y")


def make_camera(view, col):
    c = view["camera"]
    data = bpy.data.cameras.new("Extrude " + view["name"])
    data.type = c["type"]
    data.sensor_fit = "VERTICAL"
    data.sensor_height = c["sensor_height_mm"]
    data.lens = c["lens_mm"]
    data.ortho_scale = c["ortho_scale"]
    data.clip_start = c["clip_start"]
    data.clip_end = c["clip_end"]
    obj = bpy.data.objects.new("Vista: " + view["name"], data)
    col.objects.link(obj)
    place(obj, c["position"], c["rotation"])
    obj["extrude_resolution"] = [view["render"]["width"], view["render"]["height"]]
    return obj


def make_sun(view, col):
    s = view["sun"]
    data = bpy.data.lights.new("Sol " + view["name"], type="SUN")
    data.energy = s["strength"]
    data.color = s["color"]
    data.angle = math.radians(s["angle_deg"])
    obj = bpy.data.objects.new("Sol: " + view["name"], data)
    col.objects.link(obj)
    to_sun = Vector(s["to_sun"])
    aim(obj, tuple(to_sun * 100.0), (0.0, 0.0, 0.0))
    obj["extrude_azimuth_deg"] = s["azimuth_deg"]
    obj["extrude_elevation_deg"] = s["elevation_deg"]
    return obj


def make_light(view, light, col):
    data = bpy.data.lights.new("%s %s" % (view["name"], light["name"]), type=light["type"])
    data.energy = light["power_w"]
    data.color = light["color"]
    if light["type"] == "SPOT":
        data.spot_size = math.radians(light["spot_size_deg"])
        data.spot_blend = light["spot_blend"]
    if light["type"] == "AREA":
        data.shape = "RECTANGLE"
        data.size = light["size_x"]
        data.size_y = light["size_y"]
    obj = bpy.data.objects.new("Luz: %s %s" % (view["name"], light["name"]), data)
    col.objects.link(obj)
    place(obj, light["position"], light["rotation"])
    return obj


def layer_objects(imported):
    """Objetos de cada capa: el nodo con el nombre de la capa y todos sus descendientes."""
    by_layer = {}
    for obj in imported:
        name = obj.name.split(".")[0] if obj.name.rsplit(".", 1)[-1].isdigit() else obj.name
        if obj.parent is None or obj.parent not in imported:
            by_layer.setdefault(name, []).extend([obj] + list(obj.children_recursive))
    return by_layer


def set_world(world_data):
    world = bpy.context.scene.world or bpy.data.worlds.new("Extrude")
    bpy.context.scene.world = world
    if hasattr(world, "use_nodes") and not world.use_nodes:
        world.use_nodes = True  # en Blender 5 los nodos siempre están activos
    nodes = world.node_tree.nodes
    links = world.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputWorld")
    background = nodes.new("ShaderNodeBackground")
    background.inputs["Strength"].default_value = world_data["strength"]
    background.inputs["Color"].default_value = list(world_data["color"]) + [1.0]
    links.new(background.outputs["Background"], output.inputs["Surface"])
    if world_data["mode"] != "hdri" or not world_data["hdri"]:
        return
    path = os.path.join(script_dir(), world_data["hdri"])
    if not os.path.isfile(path):
        log("Aviso: no se encuentra el HDRI '%s'; se usa color de fondo." % path)
        return
    texture = nodes.new("ShaderNodeTexEnvironment")
    texture.image = bpy.data.images.load(path, check_existing=True)
    mapping = nodes.new("ShaderNodeMapping")
    coords = nodes.new("ShaderNodeTexCoord")
    mapping.inputs["Rotation"].default_value[2] = math.radians(world_data["hdri_rotation_deg"])
    links.new(coords.outputs["Generated"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], texture.inputs["Vector"])
    links.new(texture.outputs["Color"], background.inputs["Color"])


def set_color_management(view):
    settings = bpy.context.scene.view_settings
    try:
        settings.view_transform = view["view_transform"]
    except TypeError:
        log("Aviso: transformación '%s' no disponible; se mantiene '%s'."
            % (view["view_transform"], settings.view_transform))
    settings.exposure = view["exposure_stops"]


STATE = {"cameras": {}, "lights": {}, "layers": {}, "removed_defaults": []}
FACTORY_OBJECTS = {"Cube", "Light", "Camera"}


def clear_factory_scene():
    """Quita cubo, luz y cámara de la escena inicial de Blender, solo si no hay nada más."""
    scene = bpy.context.scene
    names = {o.name for o in scene.objects}
    if names != FACTORY_OBJECTS:
        return
    for name in sorted(names):
        bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
    STATE["removed_defaults"] = sorted(names)
    log("Escena de fábrica vaciada: " + ", ".join(sorted(names)))


def setup(glb_path):
    clear_factory_scene()
    imported = import_model(glb_path)
    STATE["layers"] = layer_objects(imported)
    root = collection(COLLECTION, bpy.context.scene.collection)
    for index, view in enumerate(DATA["views"]):
        col = collection("%d. %s" % (index + 1, view["name"]), root)
        STATE["cameras"][view["name"]] = make_camera(view, col)
        STATE["lights"][view["name"]] = [make_sun(view, col)] + [
            make_light(view, light, col) for light in view["lights"]
        ]
    log("Creadas %d cámaras y sus luces" % len(STATE["cameras"]))


def apply_view(name):
    view = next(v for v in DATA["views"] if v["name"] == name)
    scene = bpy.context.scene
    scene.camera = STATE["cameras"][name]
    scene.render.resolution_x = view["render"]["width"]
    scene.render.resolution_y = view["render"]["height"]
    for other, lights in STATE["lights"].items():
        for light in lights:
            light.hide_render = light.hide_viewport = other != name
    hidden = set(view["hidden_layers"])
    for layer, objects in STATE["layers"].items():
        for obj in objects:
            obj.hide_render = obj.hide_viewport = layer in hidden
    set_world(view["world"])
    set_color_management(view)
    log("Vista activa: %s (%dx%d)" % (name, view["render"]["width"], view["render"]["height"]))


def render_all(output_dir, percentage=100):
    output_dir = bpy.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    bpy.context.scene.render.resolution_percentage = percentage
    for index, view in enumerate(DATA["views"]):
        apply_view(view["name"])
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in view["name"])
        path = os.path.join(output_dir, "%02d_%s.png" % (index + 1, safe))
        bpy.context.scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        log("Render: " + path)


def write_report(path):
    scene = bpy.context.scene
    report = {
        "blender": bpy.app.version_string,
        "cameras": [
            {
                "name": obj.name,
                "type": obj.data.type,
                "lens": obj.data.lens,
                "location": list(obj.location),
                "up": list(obj.matrix_world.to_3x3() @ Vector((0.0, 1.0, 0.0))),
                "forward": list(obj.matrix_world.to_3x3() @ Vector((0.0, 0.0, -1.0))),
                "resolution": list(obj["extrude_resolution"]),
            }
            for obj in STATE["cameras"].values()
        ],
        "suns": [o.name for o in bpy.data.objects if o.type == "LIGHT" and o.data.type == "SUN"],
        "lights": [
            {"name": o.name, "type": o.data.type, "energy": o.data.energy}
            for o in bpy.data.objects
            if o.type == "LIGHT"
        ],
        "layers": {k: len(v) for k, v in STATE["layers"].items()},
        "active_camera": scene.camera.name if scene.camera else None,
        "hidden_render": sorted(o.name for o in bpy.data.objects if o.hide_render),
        "view_transform": scene.view_settings.view_transform,
        "removed_defaults": STATE["removed_defaults"],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
    log("Informe: " + path)


def main():
    options = cli_args()
    glb = options.get("glb") or os.path.join(script_dir(), DATA["model"]["file"])
    setup(glb)
    if DATA["views"]:
        apply_view(DATA["views"][0]["name"])
    if "render-all" in options:
        render_all(options["render-all"], int(options.get("percentage", "100")))
    if "report" in options:
        write_report(options["report"])


main()
`;
