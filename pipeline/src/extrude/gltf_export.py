"""Escritura GLB (glTF 2.0 binario) sin dependencias externas.

Convenciones:
- Recentrado en float64: se resta `origin` (metros, ejes DWG) antes de pasar a float32.
- Ejes: (x, y, z)_dwg → (x, z, -y)_gltf (glTF es Y-up).
- Un nodo por capa con una malla fusionada (una primitiva por material) y nodos hijos con
  `EXT_mesh_gpu_instancing` para bloques repetidos.
- Atributo de vértice `_OBJECT_ID` (float): índice del objeto dentro de la capa; los metadatos de
  cada objeto van en `node.extras.objects` como arrays paralelos.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from extrude import __version__
from extrude.build import LineObject, SceneObject
from extrude.config import Kind, LayerRule, Material
from extrude.extrusion import MeshData
from extrude.instancing import InstanceGroup, Matrix, decompose_trs, transform

AXIS_TRANSFORM_DOC = "(x, y, z)_dwg -> (x, z, -y)_gltf"
DWG_TO_GLTF: Matrix = np.array(
    [[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], dtype=np.float64
)
INSTANCING_EXT = "EXT_mesh_gpu_instancing"

FLOAT, UINT16, UINT32 = 5126, 5123, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963
MODE_LINES, MODE_TRIANGLES = 1, 4
GLB_MAGIC, GLB_VERSION = b"glTF", 2
CHUNK_JSON, CHUNK_BIN = 0x4E4F534A, 0x004E4942


@dataclass
class ExportLayer:
    name: str
    rule: LayerRule
    objects: list[SceneObject] = field(default_factory=list)
    groups: list[InstanceGroup] = field(default_factory=list)
    lines: list[LineObject] = field(default_factory=list)
    source_entities: dict[str, int] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not (self.objects or self.groups or self.lines)


@dataclass(frozen=True)
class SceneInfo:
    origin: tuple[float, float, float]  # metros, ejes DWG
    source_units: str
    crs: str | None = None


@dataclass(frozen=True)
class GlbStats:
    size_bytes: int
    triangles: int  # incluye las instancias
    draw_calls: int  # primitivas (una por nodo instanciado)
    instanced_blocks: int
    instances: int


def to_gltf_points(points: NDArray[np.float64], origin: Sequence[float]) -> NDArray[np.float32]:
    p = points - np.asarray(origin, dtype=np.float64)
    return np.column_stack([p[:, 0], p[:, 2], -p[:, 1]]).astype(np.float32)


def to_gltf_vectors(vectors: NDArray[np.float64]) -> NDArray[np.float32]:
    return np.column_stack([vectors[:, 0], vectors[:, 2], -vectors[:, 1]]).astype(np.float32)


def srgb_to_linear(hex_color: str) -> list[float]:
    rgb = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    return [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]


def vertex_normals(mesh: MeshData) -> NDArray[np.float64]:
    """Normales por vértice ponderadas por área (en DWG, Z-up)."""
    v, f = mesh.vertices, mesh.faces
    face_n = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
    normals = np.zeros_like(v)
    for k in range(3):
        np.add.at(normals, f[:, k], face_n)
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, length, out=np.zeros_like(normals), where=length > 0)
    normals[length[:, 0] == 0] = (0.0, 0.0, 1.0)
    return normals


def unweld(mesh: MeshData) -> MeshData:
    """Un vértice por esquina de triángulo: normales planas en mallas de origen 3D."""
    vertices = mesh.vertices[mesh.faces.reshape(-1)]
    return MeshData(vertices, np.arange(len(vertices), dtype=np.int64).reshape(-1, 3))


class _Builder:
    def __init__(self) -> None:
        self.blob = bytearray()
        self.doc: dict[str, Any] = {
            "asset": {"version": "2.0", "generator": f"extrude {__version__}"},
            "scenes": [{"nodes": []}],
            "scene": 0,
            "nodes": [],
            "meshes": [],
            "materials": [],
            "accessors": [],
            "bufferViews": [],
            "buffers": [],
        }
        self.triangles = 0
        self.draw_calls = 0

    def view(self, data: bytes, target: int | None) -> int:
        while len(self.blob) % 4:
            self.blob.append(0)
        view: dict[str, Any] = {"buffer": 0, "byteOffset": len(self.blob), "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        self.blob.extend(data)
        self.doc["bufferViews"].append(view)
        return len(self.doc["bufferViews"]) - 1

    def accessor(
        self,
        array: NDArray[Any],
        component: int,
        kind: str,
        target: int | None = ARRAY_BUFFER,
        bounds: bool = False,
    ) -> int:
        acc: dict[str, Any] = {
            "bufferView": self.view(np.ascontiguousarray(array).tobytes(), target),
            "componentType": component,
            "count": len(array),
            "type": kind,
        }
        if bounds:
            acc["min"] = [float(x) for x in array.min(axis=0)]
            acc["max"] = [float(x) for x in array.max(axis=0)]
        self.doc["accessors"].append(acc)
        return len(self.doc["accessors"]) - 1

    def indices(self, faces: NDArray[np.int64], vertex_count: int) -> int:
        if vertex_count <= 0xFFFF:
            return self.accessor(
                faces.reshape(-1).astype(np.uint16), UINT16, "SCALAR", ELEMENT_ARRAY_BUFFER
            )
        return self.accessor(
            faces.reshape(-1).astype(np.uint32), UINT32, "SCALAR", ELEMENT_ARRAY_BUFFER
        )

    def material(self, name: str, material: Material, kind: Kind) -> int:
        pbr = {
            "baseColorFactor": [*srgb_to_linear(material.color), material.opacity],
            "metallicFactor": material.metalness,
            "roughnessFactor": material.roughness,
        }
        entry: dict[str, Any] = {"name": name, "pbrMetallicRoughness": pbr}
        if material.opacity < 1:
            entry["alphaMode"] = "BLEND"
        if kind is Kind.LINE:
            entry["doubleSided"] = True
        self.doc["materials"].append(entry)
        return len(self.doc["materials"]) - 1

    def node(self, node: dict[str, Any], root: bool) -> int:
        self.doc["nodes"].append(node)
        index = len(self.doc["nodes"]) - 1
        if root:
            self.doc["scenes"][0]["nodes"].append(index)
        return index

    def mesh(self, name: str, primitives: list[dict[str, Any]]) -> int:
        self.doc["meshes"].append({"name": name, "primitives": primitives})
        self.draw_calls += len(primitives)
        return len(self.doc["meshes"]) - 1

    def encode(self, scene_extras: dict[str, Any]) -> bytes:
        self.doc["scenes"][0]["extras"] = scene_extras
        self.doc["buffers"] = [{"byteLength": len(self.blob)}] if self.blob else []
        for key in ("meshes", "materials", "accessors", "bufferViews", "buffers"):
            if not self.doc[key]:
                del self.doc[key]
        return encode_glb(self.doc, bytes(self.blob))


def encode_glb(doc: dict[str, Any], blob: bytes) -> bytes:
    text = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    text += b" " * (-len(text) % 4)
    blob += b"\x00" * (-len(blob) % 4)
    chunks = struct.pack("<II", len(text), CHUNK_JSON) + text
    if blob:
        chunks += struct.pack("<II", len(blob), CHUNK_BIN) + blob
    return struct.pack("<4sII", GLB_MAGIC, GLB_VERSION, 12 + len(chunks)) + chunks


def decode_glb(data: bytes) -> tuple[dict[str, Any], bytes]:
    """Inverso de encode_glb (para tests y herramientas)."""
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != GLB_MAGIC or version != GLB_VERSION or length != len(data):
        raise ValueError("No es un GLB 2.0 válido")
    json_len, _ = struct.unpack_from("<II", data, 12)
    doc = json.loads(data[20 : 20 + json_len])
    offset = 20 + json_len
    blob = b""
    if offset < len(data):
        bin_len, _ = struct.unpack_from("<II", data, offset)
        blob = data[offset + 8 : offset + 8 + bin_len]
    return doc, blob


def build_glb(layers: Sequence[ExportLayer], info: SceneInfo) -> tuple[bytes, GlbStats]:
    builder = _Builder()
    instanced_blocks = instances = 0
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    used_instancing = False

    for layer in layers:
        if layer.is_empty:
            continue
        material = builder.material(layer.name, layer.rule.effective_material, layer.rule.kind)
        primitives: list[dict[str, Any]] = []
        extras: dict[str, Any] = {
            "layer": layer.name,
            "kind": layer.rule.kind.value,
            "visible": layer.rule.visible,
            "source_entities": layer.source_entities,
        }
        triangles = 0
        if layer.objects:
            prim, tri, b = _merged_primitive(builder, layer.objects, info.origin, material)
            primitives.append(prim)
            triangles += tri
            lo, hi = np.minimum(lo, b[0]), np.maximum(hi, b[1])
            extras["objects"] = _object_table(layer.objects, layer.rule)
        if layer.lines:
            prim, b = _line_primitive(builder, layer.lines, info.origin, material)
            primitives.append(prim)
            lo, hi = np.minimum(lo, b[0]), np.maximum(hi, b[1])

        children = []
        for group in layer.groups:
            child, tri, b = _instanced_node(builder, group, info.origin, material, layer.name)
            children.append(builder.node(child, root=False))
            triangles += tri
            instanced_blocks += 1
            instances += len(group.matrices)
            used_instancing = True
            lo, hi = np.minimum(lo, b[0]), np.maximum(hi, b[1])

        extras["triangle_count"] = triangles
        builder.triangles += triangles
        node: dict[str, Any] = {"name": layer.name, "extras": extras}
        if primitives:
            node["mesh"] = builder.mesh(layer.name, primitives)
        if children:
            node["children"] = children
        builder.node(node, root=True)

    if used_instancing:
        builder.doc["extensionsUsed"] = [INSTANCING_EXT]
    bbox = {"min": lo.tolist(), "max": hi.tolist()} if np.isfinite(lo).all() else None
    scene_extras = {
        "generator": f"extrude {__version__}",
        "origin_offset": list(info.origin),
        "units": "m",
        "source_units": info.source_units,
        "crs": info.crs,
        "axis_transform": AXIS_TRANSFORM_DOC,
        "bbox": bbox,
    }
    data = builder.encode(scene_extras)
    stats = GlbStats(len(data), builder.triangles, builder.draw_calls, instanced_blocks, instances)
    return data, stats


Bounds = tuple[NDArray[np.float32], NDArray[np.float32]]


def _merged_primitive(
    builder: _Builder, objects: Sequence[SceneObject], origin: Sequence[float], material: int
) -> tuple[dict[str, Any], int, Bounds]:
    positions, normals, ids, faces = [], [], [], []
    offset = 0
    for index, obj in enumerate(objects):
        mesh = unweld(obj.mesh) if obj.hard_edges else obj.mesh
        positions.append(to_gltf_points(mesh.vertices, origin))
        normals.append(to_gltf_vectors(vertex_normals(mesh)))
        ids.append(np.full(len(mesh.vertices), index, dtype=np.float32))
        faces.append(mesh.faces + offset)
        offset += len(mesh.vertices)
    pos = np.vstack(positions)
    tri = np.vstack(faces)
    prim = {
        "attributes": {
            "POSITION": builder.accessor(pos, FLOAT, "VEC3", bounds=True),
            "NORMAL": builder.accessor(np.vstack(normals), FLOAT, "VEC3"),
            "_OBJECT_ID": builder.accessor(np.concatenate(ids), FLOAT, "SCALAR"),
        },
        "indices": builder.indices(tri, len(pos)),
        "material": material,
        "mode": MODE_TRIANGLES,
    }
    return prim, len(tri), (pos.min(axis=0), pos.max(axis=0))


def _line_primitive(
    builder: _Builder, lines: Sequence[LineObject], origin: Sequence[float], material: int
) -> tuple[dict[str, Any], Bounds]:
    positions, segments = [], []
    offset = 0
    for line in lines:
        n = len(line.points)
        if n < 2:
            continue
        idx = np.arange(n)
        pairs = np.column_stack([idx[:-1], idx[1:]])
        if line.closed and n > 2:
            pairs = np.vstack([pairs, [n - 1, 0]])
        positions.append(to_gltf_points(line.points, origin))
        segments.append(pairs + offset)
        offset += n
    pos = np.vstack(positions)
    prim = {
        "attributes": {"POSITION": builder.accessor(pos, FLOAT, "VEC3", bounds=True)},
        "indices": builder.indices(np.vstack(segments).astype(np.int64), len(pos)),
        "material": material,
        "mode": MODE_LINES,
    }
    return prim, (pos.min(axis=0), pos.max(axis=0))


def _instanced_node(
    builder: _Builder,
    group: InstanceGroup,
    origin: Sequence[float],
    material: int,
    layer: str,
) -> tuple[dict[str, Any], int, Bounds]:
    proto = unweld(group.prototype) if group.hard_edges else group.prototype
    local = to_gltf_points(proto.vertices, (0.0, 0.0, 0.0))
    prim = {
        "attributes": {
            "POSITION": builder.accessor(local, FLOAT, "VEC3", bounds=True),
            "NORMAL": builder.accessor(to_gltf_vectors(vertex_normals(proto)), FLOAT, "VEC3"),
        },
        "indices": builder.indices(proto.faces, len(local)),
        "material": material,
        "mode": MODE_TRIANGLES,
    }
    mesh_index = builder.mesh(f"{layer}/{group.block}", [prim])

    recenter = np.eye(4)
    recenter[:3, 3] = -np.asarray(origin)
    to_gltf = DWG_TO_GLTF @ recenter
    back = np.linalg.inv(DWG_TO_GLTF)
    translations, rotations, scales = [], [], []
    corners = _box_corners(proto.vertices)
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    for matrix in group.matrices:
        trs = decompose_trs(to_gltf @ matrix @ back)
        if trs is None:  # imposible: plan_instances ya lo comprobó
            raise ValueError(f"Matriz de instancia no descomponible en el bloque {group.block}")
        translations.append(trs[0])
        rotations.append(trs[1])
        scales.append(trs[2])
        world = to_gltf_points(transform(corners, matrix), origin)
        lo, hi = np.minimum(lo, world.min(axis=0)), np.maximum(hi, world.max(axis=0))
    node = {
        "name": f"{layer}/{group.block}",
        "mesh": mesh_index,
        "extensions": {
            INSTANCING_EXT: {
                "attributes": {
                    "TRANSLATION": builder.accessor(
                        np.array(translations, np.float32), FLOAT, "VEC3", None
                    ),
                    "ROTATION": builder.accessor(
                        np.array(rotations, np.float32), FLOAT, "VEC4", None
                    ),
                    "SCALE": builder.accessor(np.array(scales, np.float32), FLOAT, "VEC3", None),
                }
            }
        },
        "extras": {
            "block": group.block,
            "instances": len(group.matrices),
            "objects_per_instance": group.objects_per_instance,
        },
    }
    triangles = len(proto.faces) * len(group.matrices)
    return node, triangles, (lo.astype(np.float32), hi.astype(np.float32))


def _box_corners(vertices: NDArray[np.float64]) -> NDArray[np.float64]:
    lo, hi = vertices.min(axis=0), vertices.max(axis=0)
    return np.array(
        [[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    )


def _object_table(objects: Sequence[SceneObject], rule: LayerRule) -> dict[str, list[Any]]:
    """Metadatos por objeto (arrays paralelos). loors = altura / altura de planta (edificios);
    ase = cota de la base en metros (ejes DWG, sin recentrar en Z)."""
    building = rule.kind is Kind.BUILDING
    return {
        "height": [None if o.height is None else round(o.height, 3) for o in objects],
        "floors": [
            round(o.height / rule.floor_height) if building and o.height is not None else None
            for o in objects
        ],
        "base": [
            round(float(o.mesh.vertices[:, 2].min()), 3) if len(o.mesh.vertices) else None
            for o in objects
        ],
        "height_origin": [o.height_origin.value if o.height_origin else None for o in objects],
        "source_type": [o.source_type for o in objects],
        "handle": [o.handles[0] if o.handles else None for o in objects],
    }
