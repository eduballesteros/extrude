"""Edición geométrica de un GLB base de Extrude, sin volver a leer el DWG.

Operaciones tipadas y comprobables (las usan las herramientas de `edit_tools.py`):

- `set_height`: regenera la extrusión de un edificio de cubierta plana moviendo su coronación
  (muros y cubierta) en vertical. No escala: la planta, el zócalo y la base no cambian, las UV de
  los muros siguen en metros (v = cota) y las de la cubierta, en planta. No sirve para
  cubiertas inclinadas ni para copias de un bloque (comparten prototipo).
- `translate` / `rotate`: mueven o giran un objeto alrededor de su centro en planta (eje
  vertical). En objetos fusionados se transforman posiciones, normales y UV de las tapas (la
  textura acompaña al objeto); en copias de un bloque se edita su TRS de instancia (incluidas
  copias escaladas y giradas).
- `duplicate`: añade copias con IDs nuevos y deterministas y etiquetas nuevas.

Ejes: el GLB está en Y arriba y -Z = norte del plano; los argumentos usan metros del dibujo
(x = este, y = norte). Ninguna operación deja vértices no finitos: se valida antes y después.
"""

from __future__ import annotations

import copy
import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from extrude.errors import ExtrudeError
from extrude.gltf_export import INSTANCING_EXT, decode_glb, encode_glb
from extrude.surfaces import Surface

FLOAT = 5126
UINT32 = 5125
COMPONENTS: dict[int, Any] = {
    5120: np.int8,
    5121: np.uint8,
    5122: np.int16,
    5123: np.uint16,
    5125: np.uint32,
    5126: np.float32,
}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
EPS = 1e-3  # 1 mm: cota de coronación / cubierta plana
MIN_HEIGHT_M = 1.0
MAX_HEIGHT_M = 1_000.0
WALLS = (Surface.FACADE_EXTERIOR, Surface.FACADE_COURTYARD)
CAPS = (Surface.ROOF, Surface.UNDERSIDE, Surface.GROUND)


class EditError(ExtrudeError):
    """Operación no aplicable a ese objeto (precondición o validación)."""

    def __init__(self, code: str, problem: str, fix: str) -> None:
        super().__init__(problem, fix)
        self.code = code


class GlbEditor:
    """GLB en memoria cuyos accesores se pueden reescribir; `encode()` compacta el binario."""

    def __init__(self, data: bytes) -> None:
        doc, blob = decode_glb(data)
        self.doc: dict[str, Any] = doc
        self.blob = bytearray(blob)

    def read(self, accessor: int) -> NDArray[Any]:
        acc = self.doc["accessors"][accessor]
        view = self.doc["bufferViews"][acc["bufferView"]]
        if view.get("byteStride"):
            raise EditError(
                "layout", "GLB con datos intercalados.", "Usa el GLB de extrude convert."
            )
        dtype = COMPONENTS[acc["componentType"]]
        width = WIDTH[acc["type"]]
        start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        values = np.frombuffer(self.blob, dtype=dtype, count=acc["count"] * width, offset=start)
        return values.reshape(-1, width).copy() if width > 1 else values.copy()

    def write(self, accessor: int, values: NDArray[Any], component: int | None = None) -> None:
        """Sustituye los datos de un accesor (nueva bufferView al final; `encode` compacta)."""
        acc = self.doc["accessors"][accessor]
        component = component or acc["componentType"]
        array = np.ascontiguousarray(values, dtype=COMPONENTS[component])
        if not np.isfinite(array.astype(np.float64)).all():
            raise EditError(
                "not_finite", "La operación produjo valores no finitos.", "Revisa los argumentos."
            )
        old = self.doc["bufferViews"][acc["bufferView"]]
        self.blob += b"\x00" * (-len(self.blob) % 4)
        view: dict[str, Any] = {
            "buffer": 0,
            "byteOffset": len(self.blob),
            "byteLength": array.nbytes,
        }
        if "target" in old:
            view["target"] = old["target"]
        self.blob += array.tobytes()
        self.doc["bufferViews"].append(view)
        acc.update(
            bufferView=len(self.doc["bufferViews"]) - 1,
            byteOffset=0,
            count=len(array),
            componentType=component,
        )
        if "min" in acc or "max" in acc:
            flat = array.reshape(len(array), -1)
            acc["min"] = flat.min(axis=0).tolist()
            acc["max"] = flat.max(axis=0).tolist()

    def append(self, accessor: int, values: NDArray[Any]) -> None:
        self.write(accessor, np.concatenate([self.read(accessor), values]))

    def encode(self) -> bytes:
        """GLB válido con solo las bufferViews en uso (las sustituidas se descartan)."""
        doc = copy.deepcopy(self.doc)
        used_meshes = sorted({node["mesh"] for node in doc["nodes"] if "mesh" in node})
        mesh_indices = {old: new for new, old in enumerate(used_meshes)}
        if used_meshes:
            doc["meshes"] = [doc["meshes"][i] for i in used_meshes]
        else:
            doc.pop("meshes", None)
        for node in doc["nodes"]:
            if "mesh" in node:
                node["mesh"] = mesh_indices[node["mesh"]]
        used = sorted(
            {a["bufferView"] for a in doc.get("accessors", []) if "bufferView" in a}
            | {i["bufferView"] for i in doc.get("images", []) if "bufferView" in i}
        )
        remap: dict[int, int] = {}
        blob = bytearray()
        views: list[dict[str, Any]] = []
        for old_index in used:
            view = dict(doc["bufferViews"][old_index])
            start = view.get("byteOffset", 0)
            data = self.blob[start : start + view["byteLength"]]
            blob += b"\x00" * (-len(blob) % 4)
            view["byteOffset"] = len(blob)
            blob += data
            remap[old_index] = len(views)
            views.append(view)
        for item in doc.get("accessors", []) + doc.get("images", []):
            if "bufferView" in item:
                item["bufferView"] = remap[item["bufferView"]]
        doc["bufferViews"] = views
        doc["buffers"] = [{"byteLength": len(blob) + (-len(blob) % 4)}]
        return encode_glb(doc, bytes(blob))


# --- Localización de objetos ----------------------------------------------------------------------


@dataclass
class Located:
    object_id: str
    kind: str  # "merged" | "instance"
    node: int
    layer: str
    layer_kind: str
    floor_height: float | None
    index: int  # fila de extras.objects (fusionado) o parte (instancia)
    instance: int | None = None
    prims: list[int] = field(default_factory=list)


def locate(doc: dict[str, Any]) -> dict[str, Located]:
    """ID persistente → dónde está en el GLB (nodo de capa o copia de bloque)."""
    found: dict[str, Located] = {}

    def visit(index: int, layer: str, kind: str, floor: float | None) -> None:
        node = doc["nodes"][index]
        extras = node.get("extras", {})
        if "layer" in extras:
            layer, kind = str(extras["layer"]), str(extras.get("kind", ""))
            floor = extras.get("floor_height")
        if "mesh" in node:
            prims = [
                n
                for n, p in enumerate(doc["meshes"][node["mesh"]]["primitives"])
                if "_OBJECT_ID" in p["attributes"] and "_SURFACE" in p["attributes"]
            ]
            if INSTANCING_EXT in node.get("extensions", {}):
                for n, ids in enumerate(extras.get("object_ids", [])):
                    for part, object_id in enumerate(ids):
                        if object_id:
                            found[object_id] = Located(
                                object_id, "instance", index, layer, kind, floor, part, n, prims
                            )
            else:
                for row, object_id in enumerate(extras.get("objects", {}).get("id", [])):
                    if object_id:
                        found[object_id] = Located(
                            object_id, "merged", index, layer, kind, floor, row, None, prims
                        )
        for child in node.get("children", []):
            visit(child, layer, kind, floor)

    for root in doc["scenes"][doc.get("scene", 0)]["nodes"]:
        visit(root, "", "", None)
    return found


def _prim(editor: GlbEditor, where: Located, prim: int) -> dict[str, Any]:
    node = editor.doc["nodes"][where.node]
    p: dict[str, Any] = editor.doc["meshes"][node["mesh"]]["primitives"][prim]
    return p


def _mask(editor: GlbEditor, where: Located, prim: int) -> NDArray[np.bool_]:
    ids = np.rint(editor.read(_prim(editor, where, prim)["attributes"]["_OBJECT_ID"])).astype(int)
    return np.asarray(ids == where.index)


def _objects_table(editor: GlbEditor, where: Located) -> dict[str, list[Any]]:
    table: dict[str, list[Any]] = editor.doc["nodes"][where.node]["extras"]["objects"]
    return table


# --- Cuaterniones (orden glTF: x, y, z, w) --------------------------------------------------------


def quat_mul(a: NDArray[np.float64], b: NDArray[np.float64]) -> NDArray[np.float64]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array(
        [
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz,
        ]
    )


def quat_rotate(q: NDArray[np.float64], v: NDArray[np.float64]) -> NDArray[np.float64]:
    """Rota vectores (N × 3) con el cuaternión unitario q."""
    u = np.asarray(q[:3], dtype=np.float64)
    w = float(q[3])
    v = np.atleast_2d(np.asarray(v, dtype=np.float64))
    t = 2.0 * np.cross(u, v)
    return np.asarray(v + w * t + np.cross(u, t))


def yaw_quat(angle_deg: float) -> NDArray[np.float64]:
    """Giro en planta, antihorario visto desde arriba (de este hacia norte) = +Y en glTF."""
    half = math.radians(angle_deg) / 2
    return np.array([0.0, math.sin(half), 0.0, math.cos(half)])


def _instance_trs(
    editor: GlbEditor, where: Located
) -> tuple[NDArray[Any], NDArray[Any], NDArray[Any]]:
    attrs = editor.doc["nodes"][where.node]["extensions"][INSTANCING_EXT]["attributes"]
    return (
        editor.read(attrs["TRANSLATION"]),
        editor.read(attrs["ROTATION"]),
        editor.read(attrs["SCALE"]),
    )


def _part_bounds(editor: GlbEditor, where: Located) -> tuple[NDArray[Any], NDArray[Any]]:
    pts = []
    for prim in where.prims:
        pos = editor.read(_prim(editor, where, prim)["attributes"]["POSITION"])
        pts.append(pos[_mask(editor, where, prim)])
    stacked = np.vstack(pts) if pts else np.zeros((0, 3))
    if not len(stacked):
        raise EditError("empty", f"{where.object_id} no tiene geometría.", "Revisa el modelo.")
    return stacked.min(axis=0), stacked.max(axis=0)


def object_center(editor: GlbEditor, where: Located) -> NDArray[np.float64]:
    """Centro de la caja del objeto en coordenadas glTF del mundo (con TRS de instancia)."""
    lo, hi = _part_bounds(editor, where)
    center = (lo.astype(np.float64) + hi) / 2
    if where.kind == "instance":
        if where.instance is None:
            raise EditError("instance", "Copia sin índice.", "Revisa el modelo.")
        t, r, s = _instance_trs(editor, where)
        n = where.instance
        center = t[n] + quat_rotate(r[n].astype(np.float64), center * s[n])[0]
    return np.asarray(center, dtype=np.float64)


# --- Operaciones ----------------------------------------------------------------------------------


def instance_numbers(extras: dict[str, Any]) -> list[int]:
    """Número estable de cada copia de un bloque («#n» de su etiqueta). Quitar o independizar una
    copia no renumera las demás; las copias nuevas toman el siguiente número libre."""
    count = len(extras.get("object_ids") or []) or int(extras.get("instances", 0))
    numbers = extras.get("numbers")
    if isinstance(numbers, list) and len(numbers) == count:
        return [int(n) for n in numbers]
    return list(range(1, count + 1))


def set_height(editor: GlbEditor, where: Located, height_m: float) -> dict[str, Any]:
    """Altura del edificio (desde su planta) = `height_m`: se mueve la coronación."""
    if where.kind != "merged":
        raise EditError(
            "instance",
            "Es una copia de un bloque: cambiar su altura cambiaría todas las copias.",
            "Esta versión no edita bloques; marca edificios que no sean copias.",
        )
    if where.layer_kind != "building":
        raise EditError("not_building", f"{where.object_id} no es un edificio.", "Elige edificios.")
    if not (MIN_HEIGHT_M <= height_m <= MAX_HEIGHT_M):
        raise EditError(
            "range",
            f"Altura {height_m:.2f} m fuera de {MIN_HEIGHT_M} a {MAX_HEIGHT_M:.0f} m.",
            "Usa una altura razonable.",
        )
    table = _objects_table(editor, where)
    old = table.get("height", [None] * (where.index + 1))[where.index]
    if old is None:
        raise EditError(
            "no_height", "El edificio no tiene altura registrada.", "Vuelve a convertir."
        )
    delta = height_m - float(old)
    tops = []
    for prim in where.prims:
        attrs = _prim(editor, where, prim)["attributes"]
        mask = _mask(editor, where, prim)
        if not mask.any():
            continue
        pos = editor.read(attrs["POSITION"])
        surface = np.rint(editor.read(attrs["_SURFACE"])).astype(int)
        roof = mask & (surface == Surface.ROOF)
        if not roof.any():
            raise EditError(
                "no_roof", "El edificio no tiene cubierta.", "No se puede cambiar su altura."
            )
        top = float(pos[roof, 1].max())
        if float(np.ptp(pos[roof, 1])) > EPS:
            raise EditError(
                "sloped_roof",
                "La cubierta no es plana: subirla deformaría la geometría.",
                "Esta versión solo cambia la altura de edificios de cubierta plana.",
            )
        tops.append(top)
        moving = mask & (np.abs(pos[:, 1] - top) <= EPS)
        if (pos[mask, 1] > top + EPS).any():
            raise EditError(
                "above_roof", "Hay geometría sobre la cubierta.", "No se puede extruir."
            )
        pos[moving, 1] += delta
        editor.write(attrs["POSITION"], pos)
        uvs = editor.read(attrs["TEXCOORD_0"])
        walls = moving & np.isin(surface, [int(s) for s in WALLS])
        uvs[walls, 1] += delta  # v = cota en metros
        editor.write(attrs["TEXCOORD_0"], uvs)
    if not tops:
        raise EditError("empty", "El edificio no tiene geometría.", "Revisa el modelo.")
    table["height"][where.index] = round(height_m, 3)
    floors = table.get("floors")
    if floors is not None and where.floor_height:
        floors[where.index] = round(height_m / where.floor_height)
    return {"oldHeightM": round(float(old), 3), "newHeightM": round(height_m, 3)}


def _plan_rotate(
    xy: NDArray[np.float64], pivot: NDArray[np.float64], angle_deg: float
) -> NDArray[np.float64]:
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    d = xy - pivot
    return np.column_stack(
        [pivot[0] + c * d[:, 0] - s * d[:, 1], pivot[1] + s * d[:, 0] + c * d[:, 1]]
    )


def _transform_merged(
    editor: GlbEditor,
    where: Located,
    dx: float,
    dy: float,
    angle_deg: float,
    pivot_gltf: NDArray[np.float64],
) -> None:
    for prim in where.prims:
        attrs = _prim(editor, where, prim)["attributes"]
        mask = _mask(editor, where, prim)
        if not mask.any():
            continue
        pos = editor.read(attrs["POSITION"]).astype(np.float64)
        normals = editor.read(attrs["NORMAL"]).astype(np.float64)
        uvs = editor.read(attrs["TEXCOORD_0"]).astype(np.float64)
        surface = np.rint(editor.read(attrs["_SURFACE"])).astype(int)
        caps = mask & np.isin(surface, [int(s) for s in CAPS])
        # Planta del dibujo: X = x glTF, Y = -z glTF
        plan = np.column_stack([pos[:, 0], -pos[:, 2]])
        pivot = np.array([pivot_gltf[0], -pivot_gltf[2]])
        if caps.any():  # las UV de tapas son la planta menos un origen constante
            k = int(np.flatnonzero(caps)[0])
            origin = plan[k] - uvs[k]
        if angle_deg:
            plan[mask] = _plan_rotate(plan[mask], pivot, angle_deg)
            n_plan = np.column_stack([normals[:, 0], -normals[:, 2]])
            n_plan[mask] = _plan_rotate(n_plan[mask], np.zeros(2), angle_deg)
            normals[mask, 0] = n_plan[mask, 0]
            normals[mask, 2] = -n_plan[mask, 1]
        plan[mask] += (dx, dy)
        pos[mask, 0] = plan[mask, 0]
        pos[mask, 2] = -plan[mask, 1]
        if caps.any():
            uvs[caps] = plan[caps] - origin
        editor.write(attrs["POSITION"], pos.astype(np.float32))
        editor.write(attrs["NORMAL"], normals.astype(np.float32))
        editor.write(attrs["TEXCOORD_0"], uvs.astype(np.float32))


def _transform_instance(
    editor: GlbEditor,
    where: Located,
    dx: float,
    dy: float,
    angle_deg: float,
    pivot_gltf: NDArray[np.float64],
) -> None:
    if where.instance is None:
        raise EditError("instance", "Copia sin índice.", "Revisa el modelo.")
    attrs = editor.doc["nodes"][where.node]["extensions"][INSTANCING_EXT]["attributes"]
    t, r, _ = _instance_trs(editor, where)
    n = where.instance
    t64 = t.astype(np.float64)
    if angle_deg:
        q = yaw_quat(angle_deg)
        t64[n] = pivot_gltf + quat_rotate(q, t64[n] - pivot_gltf)[0]
        rotated = quat_mul(q, r[n].astype(np.float64))
        r = r.copy()
        r[n] = (rotated / np.linalg.norm(rotated)).astype(np.float32)
        editor.write(attrs["ROTATION"], r)
    t64[n] += (dx, 0.0, -dy)
    editor.write(attrs["TRANSLATION"], t64.astype(np.float32))


def transform(
    editor: GlbEditor,
    where: Located,
    dx: float = 0.0,
    dy: float = 0.0,
    angle_deg: float = 0.0,
    pivot: NDArray[np.float64] | None = None,
) -> dict[str, Any]:
    """Mueve (dx este, dy norte, en m) y/o gira en planta alrededor del centro del objeto (o de
    `pivot`, en glTF: los elementos de fachada giran con el centro de su edificio)."""
    if where.layer_kind not in ("building", "site", "facade_element"):
        raise EditError(
            "not_building",
            "Solo se mueven o giran edificios y copias de bloques de edificio.",
            "Los viales y el terreno están apoyados en el suelo: esta versión no los mueve.",
        )
    pivot = object_center(editor, where) if pivot is None else pivot
    if where.kind == "instance":
        _transform_instance(editor, where, dx, dy, angle_deg, pivot)
    else:
        _transform_merged(editor, where, dx, dy, angle_deg, pivot)
    return {"pivotM": [round(float(pivot[0]), 3), round(float(-pivot[2]), 3)]}


def new_object_id(source: str, salt: str) -> str:
    return "obj-" + hashlib.sha256(f"{source}|{salt}".encode()).hexdigest()[:16]


def _next_label(doc: dict[str, Any], label: str) -> str:
    match = re.match(r"^([A-Za-z]+)-(\d+)$", label or "")
    prefix = match.group(1) if match else "E"
    width = len(match.group(2)) if match else 3
    used = []
    for node in doc["nodes"]:
        for other in node.get("extras", {}).get("objects", {}).get("label", []):
            m = re.match(rf"^{re.escape(prefix)}-(\d+)$", other or "")
            if m:
                used.append(int(m.group(1)))
    return f"{prefix}-{max(used, default=0) + 1:0{width}d}"


def duplicate(
    editor: GlbEditor, where: Located, dx: float, dy: float, salt: str
) -> list[tuple[str, str]]:
    """Copia el objeto desplazada (dx, dy). Devuelve pares (ID original, ID nuevo) por pieza: una
    copia de bloque copia la instancia entera, y cada pieza conserva su correspondencia (materiales
    y trazabilidad por pieza)."""
    if where.layer_kind not in ("building", "site", "facade_element"):
        raise EditError(
            "not_building", "Solo se duplican edificios y elementos del entorno.", "Elige otros."
        )
    if where.kind == "instance":
        if where.instance is None:
            raise EditError("instance", "Copia sin índice.", "Revisa el modelo.")
        node = editor.doc["nodes"][where.node]
        attrs = node["extensions"][INSTANCING_EXT]["attributes"]
        t, r, s = _instance_trs(editor, where)
        n = where.instance
        editor.write(
            attrs["TRANSLATION"], np.vstack([t, t[n] + np.array([dx, 0.0, -dy], np.float32)])
        )
        editor.write(attrs["ROTATION"], np.vstack([r, r[n]]))
        editor.write(attrs["SCALE"], np.vstack([s, s[n]]))
        extras = node["extras"]
        source_ids = extras["object_ids"][n]
        new_ids = [new_object_id(i, salt) if i else None for i in source_ids]
        numbers = instance_numbers(extras)
        extras["numbers"] = [*numbers, max(numbers, default=0) + 1]
        extras["object_ids"].append(new_ids)
        extras["instances"] = len(extras["object_ids"])
        return [(old, new) for old, new in zip(source_ids, new_ids, strict=True) if old and new]
    table = _objects_table(editor, where)
    new_id = new_object_id(where.object_id, salt)
    row = len(table["id"])
    for column in table.values():
        if isinstance(column, list) and len(column) == row:
            column.append(copy.deepcopy(column[where.index]))
    table["id"][row] = new_id
    if "label" in table:
        table["label"][row] = _next_label(editor.doc, str(table["label"][where.index]))
    for prim in where.prims:
        p = _prim(editor, where, prim)
        attrs = p["attributes"]
        mask = _mask(editor, where, prim)
        if not mask.any():
            continue
        pos = editor.read(attrs["POSITION"])
        count = len(pos)
        remap = np.full(count, -1, dtype=np.int64)
        remap[mask] = count + np.arange(int(mask.sum()))
        copy_pos = pos[mask].copy()
        copy_pos[:, 0] += dx
        copy_pos[:, 2] -= dy
        uvs = editor.read(attrs["TEXCOORD_0"])
        surface = np.rint(editor.read(attrs["_SURFACE"])).astype(int)
        copy_uv = uvs[mask].copy()
        caps = np.isin(surface[mask], [int(s) for s in CAPS])
        copy_uv[caps] += (dx, dy)
        editor.write(attrs["POSITION"], np.vstack([pos, copy_pos]))
        editor.append(attrs["NORMAL"], editor.read(attrs["NORMAL"])[mask])
        editor.write(attrs["TEXCOORD_0"], np.vstack([uvs, copy_uv]))
        editor.append(attrs["_OBJECT_ID"], np.full(int(mask.sum()), row, dtype=np.float32))
        editor.append(attrs["_SURFACE"], editor.read(attrs["_SURFACE"])[mask])
        faces = editor.read(p["indices"]).reshape(-1, 3).astype(np.int64)
        own = mask[faces].all(axis=1)
        new_faces = remap[faces[own]]
        editor.write(
            p["indices"], np.concatenate([faces.reshape(-1), new_faces.reshape(-1)]), UINT32
        )
    return [(where.object_id, new_id)]


def current_height(editor: GlbEditor, where: Located) -> float | None:
    """Altura vigente de un edificio fusionado en el GLB que se está editando."""
    if where.kind != "merged":
        return None
    table = _objects_table(editor, where)
    value = table.get("height", [None] * (where.index + 1))[where.index]
    return None if value is None else float(value)


def unit_key(where: Located) -> tuple[int, int | str | None]:
    """Unidad física de edición: la copia de bloque entera o el objeto fusionado."""
    return (
        (where.node, where.instance) if where.kind == "instance" else (where.node, where.object_id)
    )


def unit_members(doc: dict[str, Any], where: Located) -> list[str]:
    """IDs de todas las piezas de la unidad (las piezas de una copia de bloque se mueven juntas)."""
    if where.kind != "instance" or where.instance is None:
        return [where.object_id]
    ids = doc["nodes"][where.node]["extras"]["object_ids"][where.instance]
    return [i for i in ids if i]


def geometry_check(editor: GlbEditor) -> None:
    """Validación posterior: posiciones finitas e índices dentro de rango en todo el modelo."""
    for mesh in editor.doc.get("meshes", []):
        for p in mesh["primitives"]:
            pos = editor.read(p["attributes"]["POSITION"])
            if not np.isfinite(pos).all():
                raise EditError("not_finite", "El resultado tiene vértices no finitos.", "Deshaz.")
            if "indices" in p:
                idx = editor.read(p["indices"])
                if len(idx) and int(idx.max()) >= len(pos):
                    raise EditError("indices", "Índices fuera de rango.", "Deshaz la operación.")
