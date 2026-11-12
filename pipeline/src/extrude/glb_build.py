"""Añadir geometría nueva a un GLB base editable (elementos del entorno, fachadas, áticos,
copias independizadas) conservando el contrato de Extrude: cada vértice lleva `_OBJECT_ID`,
`_SURFACE`, normal y UV en metros, y cada objeto tiene ID persistente y fila en su tabla.

Coordenadas: las mallas se construyen en ejes del dibujo YA RECENTRADOS (x este, y norte, z
arriba, mismas unidades que el GLB) y se pasan a glTF sin desplazar: `to_gltf_points(v, 0)`.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from numpy.typing import NDArray

from extrude.extrusion import MeshData
from extrude.glb_edit import FLOAT, UINT32, GlbEditor, Located, instance_numbers, quat_rotate
from extrude.gltf_export import (
    INSTANCING_EXT,
    metric_uvs,
    srgb_to_linear,
    to_gltf_points,
    to_gltf_vectors,
    vertex_normals,
    vertex_surfaces,
)

ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963


def add_accessor(
    editor: GlbEditor,
    values: NDArray[Any],
    component: int,
    kind: str,
    target: int | None = None,
    bounds: bool = False,
) -> int:
    dtype = np.uint32 if component == UINT32 else np.float32
    array = np.ascontiguousarray(values, dtype=dtype)
    editor.blob += b"\x00" * (-len(editor.blob) % 4)
    view: dict[str, Any] = {"buffer": 0, "byteOffset": len(editor.blob), "byteLength": array.nbytes}
    if target is not None:
        view["target"] = target
    editor.blob += array.tobytes()
    editor.doc.setdefault("bufferViews", []).append(view)
    accessor: dict[str, Any] = {
        "bufferView": len(editor.doc["bufferViews"]) - 1,
        "componentType": component,
        "count": len(array),
        "type": kind,
    }
    if bounds:
        flat = array.reshape(len(array), -1)
        accessor["min"] = flat.min(axis=0).tolist()
        accessor["max"] = flat.max(axis=0).tolist()
    editor.doc.setdefault("accessors", []).append(accessor)
    return len(editor.doc["accessors"]) - 1


def add_material(
    editor: GlbEditor,
    name: str,
    color: str,
    roughness: float,
    metalness: float = 0.0,
    opacity: float = 1.0,
) -> int:
    for index, material in enumerate(editor.doc.get("materials", [])):
        if material.get("name") == name:
            return index
    material = {
        "name": name,
        "pbrMetallicRoughness": {
            "baseColorFactor": [*srgb_to_linear(color), opacity],
            "metallicFactor": metalness,
            "roughnessFactor": roughness,
        },
    }
    if opacity < 1:
        material["alphaMode"] = "BLEND"
    editor.doc.setdefault("materials", []).append(material)
    return len(editor.doc["materials"]) - 1


def mesh_arrays(mesh: MeshData, object_index: float) -> dict[str, NDArray[Any]]:
    """Atributos glTF de una malla en ejes del dibujo recentrados."""
    mesh, surfaces = vertex_surfaces(mesh)
    return {
        "POSITION": to_gltf_points(mesh.vertices, (0.0, 0.0, 0.0)),
        "NORMAL": to_gltf_vectors(vertex_normals(mesh)),
        "TEXCOORD_0": metric_uvs(mesh, (0.0, 0.0)),
        "_OBJECT_ID": np.full(len(mesh.vertices), object_index, dtype=np.float32),
        "_SURFACE": surfaces.astype(np.float32),
        "indices": mesh.faces.astype(np.uint32).reshape(-1),
    }


def new_primitive(
    editor: GlbEditor, arrays: dict[str, NDArray[Any]], material: int
) -> dict[str, Any]:
    widths = {
        "POSITION": "VEC3",
        "NORMAL": "VEC3",
        "TEXCOORD_0": "VEC2",
        "_OBJECT_ID": "SCALAR",
        "_SURFACE": "SCALAR",
    }
    attributes = {
        key: add_accessor(editor, arrays[key], FLOAT, kind, ARRAY_BUFFER, bounds=key == "POSITION")
        for key, kind in widths.items()
    }
    indices = add_accessor(editor, arrays["indices"], UINT32, "SCALAR", ELEMENT_ARRAY_BUFFER)
    return {"attributes": attributes, "indices": indices, "material": material, "mode": 4}


def layer_node(editor: GlbEditor, name: str, kind: str, floor_height: float | None = None) -> int:
    """Nodo raíz de una capa (lo crea si no existe) con tabla de objetos vacía."""
    for index, node in enumerate(editor.doc["nodes"]):
        if node.get("extras", {}).get("layer") == name:
            return index
    node = {
        "name": name,
        "extras": {
            "layer": name,
            "kind": kind,
            "visible": True,
            "source_entities": 0,
            "floor_height": floor_height,
            "triangle_count": 0,
            "objects": {"id": [], "label": [], "height": [], "floors": [], "base": []},
        },
    }
    editor.doc["nodes"].append(node)
    index = len(editor.doc["nodes"]) - 1
    editor.doc["scenes"][editor.doc.get("scene", 0)]["nodes"].append(index)
    return index


def append_object(
    editor: GlbEditor,
    node_index: int,
    mesh: MeshData,
    object_id: str,
    label: str,
    row: dict[str, Any],
    material: int,
) -> int:
    """Añade un objeto fusionado a la capa `node_index` (crea su malla si no tiene). Devuelve su
    fila en la tabla de objetos."""
    node = editor.doc["nodes"][node_index]
    table: dict[str, list[Any]] = node["extras"].setdefault("objects", {"id": [], "label": []})
    index = len(table.get("id", []))
    for key in set(table) | set(row) | {"id", "label"}:
        column = table.setdefault(key, [None] * index)
        column.append(None)
    table["id"][index] = object_id
    table["label"][index] = label
    for key, value in row.items():
        table[key][index] = value
    arrays = mesh_arrays(mesh, index)
    if "mesh" not in node:
        editor.doc.setdefault("meshes", []).append(
            {
                "name": node.get("name", "capa"),
                "primitives": [new_primitive(editor, arrays, material)],
            }
        )
        node["mesh"] = len(editor.doc["meshes"]) - 1
        return index
    prims = editor.doc["meshes"][node["mesh"]]["primitives"]
    prim = next(
        (p for p in prims if "_OBJECT_ID" in p["attributes"] and p.get("mode", 4) == 4), None
    )
    if prim is None:
        prims.append(new_primitive(editor, arrays, material))
        return index
    attrs = prim["attributes"]
    count = editor.doc["accessors"][attrs["POSITION"]]["count"]
    for key in ("POSITION", "NORMAL", "TEXCOORD_0", "_OBJECT_ID", "_SURFACE"):
        editor.append(attrs[key], arrays[key])
    old = editor.read(prim["indices"]).astype(np.uint32)
    editor.write(prim["indices"], np.concatenate([old, arrays["indices"] + count]), UINT32)
    return index


def remove_object(editor: GlbEditor, where: Located) -> None:
    """Quita un objeto: su fila queda sin ID (las demás conservan su índice) y su geometría o su
    copia de bloque desaparecen."""
    node = editor.doc["nodes"][where.node]
    if where.kind == "instance":
        if where.instance is None:
            return
        attrs = node["extensions"][INSTANCING_EXT]["attributes"]
        keep = [i for i in range(len(node["extras"]["object_ids"])) if i != where.instance]
        numbers = instance_numbers(node["extras"])
        node["extras"]["numbers"] = [numbers[i] for i in keep]
        node["extras"]["object_ids"] = [node["extras"]["object_ids"][i] for i in keep]
        node["extras"]["instances"] = len(keep)
        if not keep:
            for parent in editor.doc["nodes"]:
                if where.node in parent.get("children", []):
                    parent["children"].remove(where.node)
            scene = editor.doc["scenes"][editor.doc.get("scene", 0)]["nodes"]
            if where.node in scene:
                scene.remove(where.node)
            return
        for key in ("TRANSLATION", "ROTATION", "SCALE"):
            editor.write(attrs[key], editor.read(attrs[key])[keep])
        return
    node["extras"]["objects"]["id"][where.index] = None
    removed_prims = set()
    for prim_index in where.prims:
        prim = editor.doc["meshes"][node["mesh"]]["primitives"][prim_index]
        attrs = prim["attributes"]
        ids = np.rint(editor.read(attrs["_OBJECT_ID"])).astype(int)
        keep = ids != where.index
        if keep.all():
            continue
        if not keep.any():
            removed_prims.add(prim_index)
            continue
        remap = np.cumsum(keep) - 1
        faces = editor.read(prim["indices"]).reshape(-1, 3).astype(np.int64)
        faces = faces[keep[faces].all(axis=1)]
        for key in ("POSITION", "NORMAL", "TEXCOORD_0", "_OBJECT_ID", "_SURFACE"):
            editor.write(attrs[key], editor.read(attrs[key])[keep])
        editor.write(prim["indices"], remap[faces].reshape(-1).astype(np.uint32), UINT32)
    mesh = editor.doc["meshes"][node["mesh"]]
    mesh["primitives"] = [p for i, p in enumerate(mesh["primitives"]) if i not in removed_prims]
    if not mesh["primitives"]:
        node.pop("mesh")


def add_instances(
    editor: GlbEditor,
    parent: int,
    block: str,
    parts: list[tuple[MeshData, int]],
    placements: list[tuple[NDArray[Any], float, float]],
    ids: list[str],
    extras: dict[str, Any],
) -> None:
    """Copias de un recurso (malla de varias piezas/materiales) con EXT_mesh_gpu_instancing.
    `placements`: (posición en ejes del dibujo recentrados, giro en planta °, escala uniforme).
    Reutiliza el nodo del recurso si ya existe en la capa."""
    node_index = next(
        (
            c
            for c in editor.doc["nodes"][parent].get("children", [])
            if editor.doc["nodes"][c].get("extras", {}).get("block") == block
        ),
        None,
    )
    t = np.array([to_gltf_points(np.array([p]), (0.0, 0.0, 0.0))[0] for p, _, _ in placements])
    r = np.array(
        [
            [0.0, math.sin(math.radians(a) / 2), 0.0, math.cos(math.radians(a) / 2)]
            for _, a, _ in placements
        ]
    )
    s = np.array([[k, k, k] for _, _, k in placements])
    if node_index is None:
        prims = [new_primitive(editor, mesh_arrays(mesh, 0), material) for mesh, material in parts]
        editor.doc.setdefault("meshes", []).append({"name": block, "primitives": prims})
        node = {
            "name": f"{editor.doc['nodes'][parent].get('name', '')}/{block}",
            "mesh": len(editor.doc["meshes"]) - 1,
            "extensions": {
                INSTANCING_EXT: {
                    "attributes": {
                        "TRANSLATION": add_accessor(editor, t, FLOAT, "VEC3"),
                        "ROTATION": add_accessor(editor, r, FLOAT, "VEC4"),
                        "SCALE": add_accessor(editor, s, FLOAT, "VEC3"),
                    }
                }
            },
            "extras": {
                "block": block,
                "instances": len(ids),
                "objects_per_instance": 1,
                "object_ids": [[i] for i in ids],
                **extras,
            },
        }
        editor.doc["nodes"].append(node)
        editor.doc["nodes"][parent].setdefault("children", []).append(len(editor.doc["nodes"]) - 1)
        used = editor.doc.setdefault("extensionsUsed", [])
        if INSTANCING_EXT not in used:
            used.append(INSTANCING_EXT)
        return
    node = editor.doc["nodes"][node_index]
    attrs = node["extensions"][INSTANCING_EXT]["attributes"]
    editor.append(attrs["TRANSLATION"], t.astype(np.float32))
    editor.append(attrs["ROTATION"], r.astype(np.float32))
    editor.append(attrs["SCALE"], s.astype(np.float32))
    numbers = instance_numbers(node["extras"])
    top = max(numbers, default=0)
    node["extras"]["numbers"] = numbers + list(range(top + 1, top + 1 + len(ids)))
    node["extras"]["object_ids"] += [[i] for i in ids]
    node["extras"]["instances"] = len(node["extras"]["object_ids"])


def bake_instance(
    editor: GlbEditor, where: Located
) -> tuple[list[tuple[MeshData, int]], dict[str, Any]]:
    """Geometría de una copia de bloque llevada a coordenadas del mundo (TRS completo), por pieza,
    con superficies y UV en metros. No modifica el GLB."""
    if where.instance is None:
        raise ValueError("no es una copia de bloque")
    node = editor.doc["nodes"][where.node]
    attrs = node["extensions"][INSTANCING_EXT]["attributes"]
    t = editor.read(attrs["TRANSLATION"])[where.instance].astype(np.float64)
    r = editor.read(attrs["ROTATION"])[where.instance].astype(np.float64)
    s = editor.read(attrs["SCALE"])[where.instance].astype(np.float64)
    parts: list[tuple[MeshData, int]] = []
    for prim in editor.doc["meshes"][node["mesh"]]["primitives"]:
        if "_OBJECT_ID" not in prim["attributes"]:
            continue
        a = prim["attributes"]
        ids = np.rint(editor.read(a["_OBJECT_ID"])).astype(int)
        mask = ids == where.index
        if not mask.any():
            continue
        remap = np.cumsum(mask) - 1
        faces = editor.read(prim["indices"]).reshape(-1, 3).astype(np.int64)
        faces = remap[faces[mask[faces].all(axis=1)]]
        world = t + quat_rotate(r, editor.read(a["POSITION"]).astype(np.float64)[mask] * s)
        plan = np.column_stack([world[:, 0], -world[:, 2], world[:, 1]])  # a ejes del dibujo
        surfaces = np.rint(editor.read(a["_SURFACE"])[mask]).astype(np.uint8)
        face_surfaces = surfaces[faces[:, 0]]
        uvs = editor.read(a["TEXCOORD_0"]).astype(np.float64)[mask]
        uvs[:, 0] *= float(s[0])  # u de pared en metros locales → metros reales
        parts.append((MeshData(plan, faces, face_surfaces, uvs), int(prim.get("material", 0))))
    return parts, {"translation": t.tolist(), "rotation": r.tolist(), "scale": s.tolist()}
