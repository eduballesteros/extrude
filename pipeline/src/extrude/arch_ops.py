"""Operaciones arquitectónicas y del entorno sobre el GLB editable (ver edit_tools.py).

Todas trabajan en ejes del dibujo recentrados (x este, y norte, z arriba; z = y glTF) y añaden
geometría con identidad propia (glb_build.py). Reglas comunes:

- Fachadas: aristas del contorno EXTERIOR de la huella (la unión en planta de los triángulos de
  cubierta, que conserva patios). Cada una tiene un sistema local: origen (inicio de la arista,
  cota de planta), eje horizontal u (a lo largo), eje vertical w (arriba), normal exterior n y
  orientación cardinal (sector de ±45° de n respecto al norte del plano).
- Ventanas y balcones se AÑADEN sobre la fachada (vidrio y carpintería, losa y barandilla): no
  perforan el muro. Guardan su edificio, fachada, plantas y cota superior para detectar
  conflictos si después cambia la altura o la forma.
- Retranqueo/ático: la huella pierde una franja de `d` m junto a las fachadas elegidas
  (diferencia de polígonos, válida con cóncavos y patios); puede dividirse en varias partes o
  desaparecer (se rechaza con el motivo). La planta inferior baja a la cota de inicio.
- Muros/cubierta inclinada: «subir muros» traslada la cubierta entera (forma intacta) y alarga
  los muros; «cumbrera» escala en vertical solo lo que está por encima del alero.
- Entorno: copias instanciadas de recursos procedurales; la cota de apoyo sale del terreno, de la
  superficie del objeto de referencia o de una cota dada. Nunca se inventa.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from shapely.geometry import LineString, MultiPoint, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from extrude.extrusion import MeshData, concat, extrude, labeled
from extrude.glb_build import (
    add_instances,
    add_material,
    append_object,
    bake_instance,
    layer_node,
    remove_object,
)
from extrude.glb_edit import EPS, EditError, GlbEditor, Located, locate, new_object_id
from extrude.site_assets import ASSETS
from extrude.surfaces import Surface

MARGIN_M = 0.5
FACADE_LAYERS = {
    "window_glass": ("VENTANAS", "#9fb8c8", 0.1, 0.0, 0.45),
    "window_frame": ("CARPINTERIAS", "#2d3236", 0.5, 0.6, 1.0),
    "balcony": ("BALCONES", "#b9b6ae", 0.85, 0.0, 1.0),
}
SITE_LAYER = "ENTORNO"


# --- Lectura de un edificio -----------------------------------------------------------------------


@dataclass
class Building:
    where: Located
    points: NDArray[np.float64]  # vértices del objeto, ejes del dibujo (x, y, z)
    surfaces: NDArray[np.int64]
    roof_triangles: list[NDArray[np.float64]]
    floor_z: float
    top_z: float
    eave_z: float

    @property
    def footprint(self) -> Polygon:
        tris = [Polygon(t[:, :2]) for t in self.roof_triangles if Polygon(t[:, :2]).area > 1e-9]
        shape = unary_union(tris).buffer(0)
        if shape.geom_type == "MultiPolygon":
            shape = max(shape.geoms, key=lambda g: g.area)
        return orient(shape.simplify(0.01), 1.0)


def read_building(editor: GlbEditor, where: Located) -> Building:
    if where.kind != "merged":
        raise EditError(
            "instance",
            "Es una copia de bloque: independízala antes.",
            "Usa block.make_unique sobre esa copia.",
        )
    node = editor.doc["nodes"][where.node]
    pts, surfs, roofs = [], [], []
    for prim_index in where.prims:
        prim = editor.doc["meshes"][node["mesh"]]["primitives"][prim_index]
        a = prim["attributes"]
        ids = np.rint(editor.read(a["_OBJECT_ID"])).astype(int)
        mask = ids == where.index
        if not mask.any():
            continue
        pos = editor.read(a["POSITION"]).astype(np.float64)
        plan = np.column_stack([pos[:, 0], -pos[:, 2], pos[:, 1]])
        surface = np.rint(editor.read(a["_SURFACE"])).astype(int)
        faces = editor.read(prim["indices"]).reshape(-1, 3).astype(np.int64)
        own = faces[mask[faces].all(axis=1)]
        roof = own[(surface[own] == Surface.ROOF).all(axis=1)]
        roofs += [plan[f] for f in roof]
        pts.append(plan[mask])
        surfs.append(surface[mask])
    if not roofs:
        raise EditError("no_roof", "El edificio no tiene cubierta reconocible.", "Elige otro.")
    points = np.vstack(pts)
    surfaces = np.concatenate(surfs)
    facade = points[np.isin(surfaces, [Surface.FACADE_EXTERIOR, Surface.FACADE_COURTYARD])]
    roof_z = np.concatenate([t[:, 2] for t in roofs])
    floor_z = float(facade[:, 2].min()) if len(facade) else float(points[:, 2].min())
    return Building(
        where, points, surfaces, roofs, floor_z, float(roof_z.max()), float(roof_z.min())
    )


# --- Fachadas -------------------------------------------------------------------------------------

CARDINAL = {"east": 0.0, "north": 90.0, "west": 180.0, "south": 270.0}


@dataclass(frozen=True)
class Facade:
    id: str
    start: NDArray[np.float64]  # (x, y)
    u: NDArray[np.float64]  # eje horizontal unitario a lo largo de la fachada
    normal: NDArray[np.float64]  # normal exterior unitaria
    length: float
    orientation: str

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "orientation": self.orientation,
            "lengthM": round(self.length, 2),
            "origin": [round(float(c), 2) for c in self.start],
            "u": [round(float(c), 3) for c in self.u],
            "normal": [round(float(c), 3) for c in self.normal],
        }


def facades(building: Building) -> list[Facade]:
    ring = list(building.footprint.exterior.coords)[:-1]
    out = []
    for i, (a, b) in enumerate(zip(ring, ring[1:] + ring[:1], strict=True)):
        p0, p1 = np.array(a), np.array(b)
        d = p1 - p0
        length = float(np.hypot(*d))
        if length < 0.05:
            continue
        u = d / length
        normal = np.array([u[1], -u[0]])  # anillo antihorario: la normal exterior a la derecha
        angle = math.degrees(math.atan2(normal[1], normal[0])) % 360
        side = min(
            CARDINAL, key=lambda k: min(abs(angle - CARDINAL[k]), 360 - abs(angle - CARDINAL[k]))
        )
        out.append(Facade(f"f{i}", p0, u, normal, length, side))
    return out


def facades_facing(building: Building, orientation: str) -> list[Facade]:
    found = [f for f in facades(building) if f.orientation == orientation]
    if not found:
        raise EditError(
            "no_facade",
            f"El edificio no tiene fachada orientada al {orientation}.",
            "Elige otra orientación o marca la fachada.",
        )
    return found


def _panel(f: Facade, s0: float, s1: float, n0: float, n1: float, z0: float, h: float) -> MeshData:
    """Caja alineada con la fachada: de s0 a s1 a lo largo, de n0 a n1 hacia fuera."""
    a = f.start + f.u * s0 + f.normal * n0
    b = f.start + f.u * s1 + f.normal * n0
    c = f.start + f.u * s1 + f.normal * n1
    d = f.start + f.u * s0 + f.normal * n1
    poly = orient(Polygon([a, b, c, d]), 1.0)
    return labeled(extrude(poly, z0, h, bottom=True), Surface.GENERIC)


def hosted(editor: GlbEditor, host_id: str) -> list[tuple[Located, dict[str, Any]]]:
    """Elementos de fachada (ventanas, carpinterías, balcones) que pertenecen a un edificio."""
    found = []
    for where in locate(editor.doc).values():
        if where.kind != "merged":
            continue
        table = editor.doc["nodes"][where.node]["extras"].get("objects", {})
        if (table.get("host") or [None] * (where.index + 1))[where.index] == host_id:
            found.append(
                (where, {k: v[where.index] for k, v in table.items() if isinstance(v, list)})
            )
    return found


def hosted_conflicts(editor: GlbEditor, host_id: str, new_top_z: float) -> list[str]:
    return [
        str(row.get("label"))
        for _, row in hosted(editor, host_id)
        if row.get("top_z") is not None and float(row["top_z"]) > new_top_z + EPS
    ]


def _floors(building: Building, floor_height: float | None, wanted: list[int] | None) -> list[int]:
    if not floor_height:
        raise EditError(
            "no_floor_height",
            "Falta la altura de planta para situar los elementos.",
            "Indícala (floorHeightM) o configura floor_height en la capa.",
        )
    total = max(1, round((building.top_z - building.floor_z) / floor_height))
    floors = list(range(total)) if wanted is None else sorted(set(wanted))
    bad = [f for f in floors if f < 0 or f >= total]
    if bad:
        raise EditError(
            "floor_range",
            f"Plantas {bad} fuera del edificio (0 a {total - 1}).",
            "Elige plantas existentes (0 = baja).",
        )
    return floors


def _existing(
    editor: GlbEditor, host_id: str, facade_id: str, kind: str, floors: list[int]
) -> bool:
    for _, row in hosted(editor, host_id):
        same = row.get("facade") == facade_id and row.get("element") == kind
        if same and set(row.get("floor_list") or []) & set(floors):
            return True
    return False


def add_windows(
    editor: GlbEditor,
    where: Located,
    orientation: str,
    args: dict[str, Any],
    floor_height: float | None,
    salt: str,
    label: str,
) -> list[str]:
    building = read_building(editor, where)
    w, h, sill = args["widthM"], args["heightM"], args["sillM"]
    depth, gap = args["depthM"], args["spacingM"]
    floors = _floors(building, args.get("floorHeightM") or floor_height, args.get("floors"))
    fh = float(args.get("floorHeightM") or floor_height or 0)
    if sill + h > fh - 0.1:
        raise EditError(
            "too_tall",
            f"Antepecho {sill} m + ventana {h} m no caben en una planta de {fh} m.",
            "Reduce la altura o el antepecho.",
        )
    created = []
    for facade in facades_facing(building, orientation):
        usable = facade.length - 2 * MARGIN_M
        columns = args.get("columns") or int((usable + gap) // (w + gap))
        if columns < 1 or columns * w + (columns - 1) * gap > usable + 1e-6:
            raise EditError(
                "no_fit",
                f"No caben ventanas de {w} m en la fachada {facade.id} ({facade.length:.1f} m).",
                "Reduce la anchura, la separación o las columnas.",
            )
        if _existing(editor, where.object_id, facade.id, "window", floors):
            raise EditError(
                "duplicate",
                f"Ya hay ventanas en la fachada {facade.id} de esas plantas.",
                "Elimínalas antes o elige otras plantas.",
            )
        start = (facade.length - (columns * w + (columns - 1) * gap)) / 2
        glass, frame = [], []
        frame_w = 0.06
        for f in floors:
            z0 = building.floor_z + f * fh + sill
            for k in range(columns):
                s0 = start + k * (w + gap)
                glass.append(_panel(facade, s0, s0 + w, 0.01, 0.01 + depth / 2, z0, h))
                for a, b, zz, hh in (
                    (s0 - frame_w, s0, z0, h),
                    (s0 + w, s0 + w + frame_w, z0, h),
                    (s0 - frame_w, s0 + w + frame_w, z0 - frame_w, frame_w),
                    (s0 - frame_w, s0 + w + frame_w, z0 + h, frame_w),
                ):
                    frame.append(_panel(facade, a, b, 0.01, 0.01 + depth, zz, hh))
        top_z = building.floor_z + max(floors) * fh + sill + h + frame_w
        base_row = {
            "host": where.object_id,
            "facade": facade.id,
            "element": "window",
            "floor_list": floors,
            "top_z": round(top_z, 3),
            "orientation": orientation,
        }
        for kind, meshes in (("window_glass", glass), ("window_frame", frame)):
            layer, color, rough, metal, opacity = FACADE_LAYERS[kind]
            node = layer_node(editor, layer, "facade_element")
            material = add_material(editor, f"facade:{kind}", color, rough, metal, opacity)
            new_id = new_object_id(f"{where.object_id}:{facade.id}:{kind}", salt)
            append_object(
                editor,
                node,
                concat(meshes),
                new_id,
                f"{label} {'ventanas' if kind == 'window_glass' else 'carpinterías'} "
                f"{orientation} {facade.id}",
                {**base_row, "height": round(h, 3)},
                material,
            )
            created.append(new_id)
    return created


def add_balconies(
    editor: GlbEditor,
    where: Located,
    orientation: str,
    args: dict[str, Any],
    floor_height: float | None,
    salt: str,
    label: str,
) -> list[str]:
    building = read_building(editor, where)
    w, proj, thick = args["widthM"], args["projectionM"], args["thicknessM"]
    rail = args["railingHeightM"]
    fh = float(args.get("floorHeightM") or floor_height or 0)
    floors = _floors(building, fh or None, args.get("floors"))
    if 0 in floors:
        raise EditError(
            "ground_floor",
            "Un balcón en planta baja no tiene sentido.",
            "Elige plantas 1 o superiores.",
        )
    created = []
    layer, color, rough, metal, opacity = FACADE_LAYERS["balcony"]
    node = layer_node(editor, layer, "facade_element")
    material = add_material(editor, "facade:balcony", color, rough, metal, opacity)
    for facade in facades_facing(building, orientation):
        if w > facade.length - 2 * MARGIN_M:
            raise EditError(
                "no_fit",
                f"Un balcón de {w} m no cabe en la fachada {facade.id} ({facade.length:.1f} m).",
                "Reduce la anchura.",
            )
        if _existing(editor, where.object_id, facade.id, "balcony", floors):
            raise EditError(
                "duplicate",
                f"Ya hay balcones en la fachada {facade.id} de esas plantas.",
                "Elimínalos antes o elige otras plantas.",
            )
        s0 = (facade.length - w) / 2
        parts = []
        for f in floors:
            z = building.floor_z + f * fh
            parts.append(_panel(facade, s0, s0 + w, 0.0, proj, z - thick, thick))
            parts.append(_panel(facade, s0, s0 + w, proj - 0.05, proj, z, rail))
            parts.append(_panel(facade, s0, s0 + 0.05, 0.0, proj, z, rail))
            parts.append(_panel(facade, s0 + w - 0.05, s0 + w, 0.0, proj, z, rail))
        top_z = building.floor_z + max(floors) * fh + rail
        new_id = new_object_id(f"{where.object_id}:{facade.id}:balcony", salt)
        append_object(
            editor,
            node,
            concat(parts),
            new_id,
            f"{label} balcones {orientation} {facade.id}",
            {
                "host": where.object_id,
                "facade": facade.id,
                "element": "balcony",
                "floor_list": floors,
                "top_z": round(top_z, 3),
                "orientation": orientation,
                "height": rail,
            },
            material,
        )
        created.append(new_id)
    return created


# --- Muros, cubierta y retranqueo -----------------------------------------------------------------


def _move_above(editor: GlbEditor, where: Located, level: float, fn: Any) -> None:
    """Aplica `fn(z) -> z'` a los vértices del objeto con cota ≥ level (y ajusta UV de muros)."""
    node = editor.doc["nodes"][where.node]
    for prim_index in where.prims:
        prim = editor.doc["meshes"][node["mesh"]]["primitives"][prim_index]
        a = prim["attributes"]
        mask = np.rint(editor.read(a["_OBJECT_ID"])).astype(int) == where.index
        pos = editor.read(a["POSITION"]).astype(np.float64)
        moving = mask & (pos[:, 1] >= level - EPS)
        if not moving.any():
            continue
        new = fn(pos[moving, 1])
        delta = new - pos[moving, 1]
        pos[moving, 1] = new
        uvs = editor.read(a["TEXCOORD_0"]).astype(np.float64)
        surface = np.rint(editor.read(a["_SURFACE"])).astype(int)
        walls = np.isin(surface[moving], [Surface.FACADE_EXTERIOR, Surface.FACADE_COURTYARD])
        idx = np.flatnonzero(moving)[walls]
        uvs[idx, 1] += delta[walls]
        editor.write(a["POSITION"], pos.astype(np.float32))
        editor.write(a["TEXCOORD_0"], uvs.astype(np.float32))


def raise_walls(editor: GlbEditor, where: Located, delta: float) -> dict[str, Any]:
    """Alarga los muros `delta` m y traslada la cubierta (plana o inclinada) sin deformarla."""
    building = read_building(editor, where)
    if building.top_z - building.floor_z + delta < 1.0:
        raise EditError("range", "La altura resultante sería menor de 1 m.", "Usa un valor menor.")
    if delta < 0 and hosted_conflicts(editor, where.object_id, building.top_z + delta):
        raise EditError(
            "hosted",
            "Hay ventanas o balcones por encima de la nueva altura: "
            + ", ".join(hosted_conflicts(editor, where.object_id, building.top_z + delta)[:4]),
            "Elimínalos antes o baja menos.",
        )
    _move_above(editor, where, building.eave_z, lambda z: z + delta)
    _update_height(editor, where, delta)
    return {"eaveM": round(building.eave_z + delta, 3), "topM": round(building.top_z + delta, 3)}


def set_ridge(editor: GlbEditor, where: Located, ridge: float) -> dict[str, Any]:
    """Altura de cumbrera sobre el alero: escala en vertical lo que está por encima del alero
    (cubierta y hastiales), con el alero y la huella fijos."""
    building = read_building(editor, where)
    current = building.top_z - building.eave_z
    if current < 0.1:
        raise EditError(
            "flat_roof",
            "La cubierta es plana: no tiene cumbrera.",
            "Usa building.set_height o building.raise_walls.",
        )
    if not (0.3 <= ridge <= 30):
        raise EditError(
            "range", f"Cumbrera {ridge} m fuera de 0,3 a 30 m.", "Usa un valor razonable."
        )
    k = ridge / current
    eave = building.eave_z
    _move_above(editor, where, eave + EPS, lambda z: eave + (z - eave) * k)
    _update_frames(editor, where)
    _update_height(editor, where, ridge - current)
    return {"ridgeFromM": round(current, 3), "ridgeToM": round(ridge, 3)}


def _update_frames(editor: GlbEditor, where: Located) -> None:
    """Normales y tangentes coherentes con la geometría y UV finales, sin unir aristas partidas."""
    node = editor.doc["nodes"][where.node]
    for prim_index in where.prims:
        prim = editor.doc["meshes"][node["mesh"]]["primitives"][prim_index]
        attrs = prim["attributes"]
        mask = np.rint(editor.read(attrs["_OBJECT_ID"])).astype(int) == where.index
        if not mask.any():
            continue
        pos = editor.read(attrs["POSITION"]).astype(np.float64)
        faces = editor.read(prim["indices"]).reshape(-1, 3).astype(int)
        edge1, edge2 = pos[faces[:, 1]] - pos[faces[:, 0]], pos[faces[:, 2]] - pos[faces[:, 0]]
        sums = np.zeros_like(pos)
        face_normals = np.cross(edge1, edge2)
        for corner in range(3):
            np.add.at(sums, faces[:, corner], face_normals)
        lengths = np.linalg.norm(sums, axis=1)
        valid = mask & (lengths > 1e-12)
        normals = editor.read(attrs["NORMAL"]).astype(np.float64)
        normals[valid] = sums[valid] / lengths[valid, None]
        editor.write(attrs["NORMAL"], normals)
        if "TANGENT" not in attrs:
            continue
        uv = editor.read(attrs["TEXCOORD_0"])
        duv1, duv2 = uv[faces[:, 1]] - uv[faces[:, 0]], uv[faces[:, 2]] - uv[faces[:, 0]]
        det = duv1[:, 0] * duv2[:, 1] - duv1[:, 1] * duv2[:, 0]
        inv = np.zeros_like(det)
        np.divide(1.0, det, out=inv, where=np.abs(det) > 1e-12)
        u = (edge1 * duv2[:, 1, None] - edge2 * duv1[:, 1, None]) * inv[:, None]
        v = (edge2 * duv1[:, 0, None] - edge1 * duv2[:, 0, None]) * inv[:, None]
        tangent, bitangent = np.zeros_like(pos), np.zeros_like(pos)
        for corner in range(3):
            np.add.at(tangent, faces[:, corner], u)
            np.add.at(bitangent, faces[:, corner], v)
        tangent -= normals * (normals * tangent).sum(axis=1)[:, None]
        lengths = np.linalg.norm(tangent, axis=1)
        tangents = editor.read(attrs["TANGENT"]).astype(np.float64)
        for index in np.flatnonzero(mask):
            t = tangent[index]
            if lengths[index] <= 1e-12:
                axis = np.eye(3)[int(np.argmin(np.abs(normals[index])))]
                t = np.cross(axis, normals[index])
            t = t / max(float(np.linalg.norm(t)), 1e-12)
            tangents[index, :3] = t
            tangents[index, 3] = (
                -1 if np.dot(np.cross(normals[index], t), bitangent[index]) < 0 else 1
            )
        editor.write(attrs["TANGENT"], tangents)


def _update_height(editor: GlbEditor, where: Located, delta: float) -> None:
    table = editor.doc["nodes"][where.node]["extras"]["objects"]
    if table.get("height") and table["height"][where.index] is not None:
        table["height"][where.index] = round(float(table["height"][where.index]) + delta, 3)


def setback(
    editor: GlbEditor,
    where: Located,
    args: dict[str, Any],
    label: str,
    salt: str,
    set_height: Any,
) -> tuple[list[str], dict[str, Any]]:
    """Ático retranqueado: el volumen original queda hasta la cota de inicio y encima se crea un
    volumen nuevo cuya huella pierde `setbackM` junto a las fachadas elegidas."""
    building = read_building(editor, where)
    start, d, upper = args["startHeightM"], args["setbackM"], args["upperHeightM"]
    height = building.top_z - building.floor_z
    if abs(building.top_z - building.eave_z) > 0.1:
        raise EditError(
            "sloped_roof",
            "El retranqueo necesita una cubierta plana.",
            "Esta versión no retranquea edificios de cubierta inclinada.",
        )
    if not (1.0 <= start < height - EPS):
        raise EditError(
            "start",
            f"La cota de inicio ({start} m) debe estar entre 1 m y la altura "
            f"actual ({height:.2f} m).",
            "Elige una cota menor.",
        )
    conflicts = hosted_conflicts(editor, where.object_id, building.floor_z + start)
    if conflicts:
        raise EditError(
            "hosted",
            "Hay ventanas o balcones por encima de la cota de inicio: " + ", ".join(conflicts[:4]),
            "Elimínalos antes.",
        )
    footprint = building.footprint
    wanted = set(args["orientations"])
    edges = [f for f in facades(building) if f.orientation in wanted or "all" in wanted]
    if not edges:
        raise EditError("no_facade", "Ninguna fachada con esa orientación.", "Revisa las fachadas.")
    strips = unary_union(
        [LineString([f.start, f.start + f.u * f.length]).buffer(d, cap_style="flat") for f in edges]
    )
    remaining = footprint.difference(strips).buffer(0)
    parts = [g for g in getattr(remaining, "geoms", [remaining]) if g.area >= 1.0]
    if not parts:
        raise EditError(
            "vanishes",
            f"Con {d} m de retranqueo el volumen superior desaparece.",
            "Reduce el retranqueo o afecta a menos fachadas.",
        )
    set_height(editor, where, start)
    node = where.node
    table = editor.doc["nodes"][node]["extras"]["objects"]
    material = editor.doc["meshes"][editor.doc["nodes"][node]["mesh"]]["primitives"][
        where.prims[0]
    ]["material"]
    created = []
    for n, part in enumerate(sorted(parts, key=lambda g: -g.area), start=1):
        mesh = extrude(orient(part, 1.0), building.floor_z + start, upper, bottom=False)
        new_id = new_object_id(f"{where.object_id}:setback:{n}", salt)
        append_object(
            editor,
            node,
            mesh,
            new_id,
            f"{label}.A{n}",
            {
                "height": round(upper, 3),
                "base": table.get("base", [None] * (where.index + 1))[where.index],
                "parent": where.object_id,
            },
            material,
        )
        created.append(new_id)
    return created, {
        "parts": len(parts),
        "upperAreaM2": round(sum(p.area for p in parts), 1),
        "lowerHeightM": start,
    }


# --- Copias de bloque -----------------------------------------------------------------------------


def make_unique(editor: GlbEditor, where: Located, label_of: dict[str, str]) -> list[str]:
    """Convierte una copia de bloque en geometría propia (TRS completo aplicado), con los MISMOS
    IDs y etiquetas por pieza; las demás copias no cambian."""
    if where.kind != "instance" or where.instance is None:
        raise EditError("not_instance", "No es una copia de bloque.", "Ya es independiente.")
    parent = next(
        i for i, n in enumerate(editor.doc["nodes"]) if where.node in n.get("children", [])
    )
    ids = [i for i in editor.doc["nodes"][where.node]["extras"]["object_ids"][where.instance] if i]
    located = locate(editor.doc)
    baked: list[tuple[str, MeshData, int]] = []
    for object_id in ids:
        parts, _ = bake_instance(editor, located[object_id])
        mesh = concat([m for m, _ in parts])
        baked.append((object_id, mesh, parts[0][1]))
    remove_object(editor, located[ids[0]])
    for object_id, mesh, material in baked:
        z = mesh.vertices[:, 2]
        append_object(
            editor,
            parent,
            mesh,
            object_id,
            label_of.get(object_id, object_id),
            {
                "height": round(float(z.max() - z.min()), 3),
                "base": None,
                "unique_of": editor.doc["nodes"][where.node]["extras"].get("block"),
            },
            material,
        )
    return ids


# --- Entorno --------------------------------------------------------------------------------------


def surface_heights(
    editor: GlbEditor,
    xy: NDArray[np.float64],
    kinds: set[str] | None,
    ids: set[str] | None,
) -> NDArray[np.float64]:
    """Cota más alta bajo cada punto en planta (NaN si no hay superficie): terreno (`kinds`) o la
    superficie de unos objetos concretos (`ids`)."""
    tris = []
    for where in locate(editor.doc).values():
        if (
            where.kind != "merged"
            or (kinds and where.layer_kind not in kinds)
            or (ids and where.object_id not in ids)
        ):
            continue
        node = editor.doc["nodes"][where.node]
        for prim_index in where.prims:
            prim = editor.doc["meshes"][node["mesh"]]["primitives"][prim_index]
            a = prim["attributes"]
            mask = np.rint(editor.read(a["_OBJECT_ID"])).astype(int) == where.index
            pos = editor.read(a["POSITION"]).astype(np.float64)
            plan = np.column_stack([pos[:, 0], -pos[:, 2], pos[:, 1]])
            faces = editor.read(prim["indices"]).reshape(-1, 3).astype(np.int64)
            tris.append(plan[faces[mask[faces].all(axis=1)]])
    out = np.full(len(xy), np.nan)
    if not tris:
        return out
    t = np.concatenate(tris)
    a, b, c = t[:, 0], t[:, 1], t[:, 2]
    for i, (x, y) in enumerate(xy):
        v0, v1 = c[:, :2] - a[:, :2], b[:, :2] - a[:, :2]
        v2 = np.array([x, y]) - a[:, :2]
        d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
        d02, d12 = (v0 * v2).sum(1), (v1 * v2).sum(1)
        den = d00 * d11 - d01 * d01
        ok = np.abs(den) > 1e-12
        with np.errstate(divide="ignore", invalid="ignore"):
            uu = np.where(ok, (d11 * d02 - d01 * d12) / den, -1)
            vv = np.where(ok, (d00 * d12 - d01 * d02) / den, -1)
        hit = (uu >= -1e-6) & (vv >= -1e-6) & (uu + vv <= 1 + 1e-6)
        if hit.any():
            z = a[hit, 2] + uu[hit] * (c[hit, 2] - a[hit, 2]) + vv[hit] * (b[hit, 2] - a[hit, 2])
            out[i] = float(z.max())
    return out


def object_plan_points(editor: GlbEditor, where: Located) -> NDArray[np.float64]:
    node = editor.doc["nodes"][where.node]
    pts = []
    for prim_index in where.prims:
        prim = editor.doc["meshes"][node["mesh"]]["primitives"][prim_index]
        a = prim["attributes"]
        mask = np.rint(editor.read(a["_OBJECT_ID"])).astype(int) == where.index
        pos = editor.read(a["POSITION"]).astype(np.float64)[mask]
        pts.append(np.column_stack([pos[:, 0], -pos[:, 2]]))
    return np.vstack(pts)


def layout_points(
    editor: GlbEditor,
    args: dict[str, Any],
    targets: list[Located],
    center: tuple[float, float],
) -> list[tuple[NDArray[np.float64], float]]:
    """Posiciones en planta y giro (°) de cada elemento según la distribución pedida."""
    count, spacing = int(args["count"]), args.get("spacingM")
    layout = args["layout"]
    if layout == "point":
        if args.get("xM") is None or args.get("yM") is None:
            raise EditError("no_point", "Falta la posición (xM, yM).", "Indica dónde colocarlo.")
        base = np.array([center[0] + args["xM"], center[1] + args["yM"]])
        step = spacing or 0.0
        return [
            (base + np.array([k * step, 0.0]), args.get("rotationDeg") or 0.0) for k in range(count)
        ]
    if not targets:
        raise EditError(
            "no_target",
            "Esta distribución necesita un objeto de referencia.",
            "Marca el camino, la plaza o el edificio.",
        )
    pts = np.vstack([object_plan_points(editor, w) for w in targets])
    hull = MultiPoint([tuple(p) for p in pts]).convex_hull
    if layout == "around":
        centre = np.array(hull.centroid.coords[0])
        offset = float(args.get("sideOffsetM", 0.0))
        boundary_shape = hull.buffer(offset, join_style="mitre") if offset else hull
        if boundary_shape.geom_type != "Polygon":
            boundary_shape = hull.buffer(max(offset, 0.5))
        boundary = boundary_shape.exterior
        out = []
        for k in range(count):
            point = boundary.interpolate(boundary.length * k / count)
            pos = np.array(point.coords[0])
            facing = math.degrees(math.atan2(*(centre - pos)[::-1])) - 90  # frente (+Y) al centro
            out.append(
                (pos, facing if args["facing"] == "target" else (args.get("rotationDeg") or 0.0))
            )
        return out
    rect = hull.minimum_rotated_rectangle
    corners = np.array(rect.exterior.coords)[:4]
    edges = [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    long_a, long_b = max(edges, key=lambda e: np.hypot(*(e[1] - e[0])))
    axis = (long_b - long_a) / np.hypot(*(long_b - long_a))
    across = np.array([-axis[1], axis[0]])
    mid = np.array(rect.centroid.coords[0])
    length = float(np.hypot(*(long_b - long_a)))
    spacing = spacing or length / max(count, 1)
    needed = (count - 1) * spacing
    if needed > length + 1e-6:
        raise EditError(
            "no_fit",
            f"{count} elementos a {spacing} m necesitan {needed:.1f} m y el "
            f"objeto mide {length:.1f} m.",
            "Reduce la cantidad o la separación.",
        )
    width = min(np.hypot(*(e[1] - e[0])) for e in edges)
    offsets = {
        "center": [0.0],
        "left": [width / 2 + args["sideOffsetM"]],
        "right": [-(width / 2 + args["sideOffsetM"])],
        "both": [width / 2 + args["sideOffsetM"], -(width / 2 + args["sideOffsetM"])],
    }[args["sides"]]
    yaw_along = math.degrees(math.atan2(axis[1], axis[0])) - 90
    out = []
    for off in offsets:
        for k in range(count):
            pos = mid + axis * (-needed / 2 + k * spacing) + across * off
            if args["facing"] == "along":
                yaw = yaw_along
            elif args["facing"] == "target":
                yaw = math.degrees(math.atan2(*(-across * np.sign(off or 1))[::-1])) - 90
            else:
                yaw = args.get("rotationDeg") or 0.0
            out.append((pos, yaw))
    return out


def place_site(
    editor: GlbEditor,
    args: dict[str, Any],
    targets: list[Located],
    center: tuple[float, float],
    origin_z: float,
    salt: str,
) -> list[str]:
    asset = ASSETS[args["assetId"]]
    placements = layout_points(editor, args, targets, center)
    xy = np.array([p for p, _ in placements])
    if args["support"] == "terrain":
        z = surface_heights(editor, xy, {"terrain"}, None)
    elif args["support"] == "target_surface":
        z = surface_heights(editor, xy, None, {w.object_id for w in targets})
    else:
        if args.get("baseZM") is None:
            raise EditError("no_elevation", "Falta la cota de apoyo.", "Indica baseZM (m).")
        z = np.full(len(xy), float(args["baseZM"]) - origin_z)
    missing = int(np.isnan(z).sum())
    if missing:
        raise EditError(
            "no_support",
            f"{missing} de {len(z)} elementos no tienen superficie de apoyo debajo.",
            "Usa una cota (support=elevation) o acércalos a la superficie.",
        )
    parent = layer_node(editor, SITE_LAYER, "site")
    parts = [
        (
            p.mesh,
            add_material(editor, f"site:{asset.id}:{p.material}", *asset.materials[p.material]),
        )
        for p in asset.parts
    ]
    ids = [new_object_id(f"{asset.id}:{k}", salt) for k in range(len(placements))]
    add_instances(
        editor,
        parent,
        asset.block,
        parts,
        [
            (np.array([xy[k, 0], xy[k, 1], z[k]]), yaw, float(args["scale"]))
            for k, (_, yaw) in enumerate(placements)
        ],
        ids,
        {"asset": {"id": asset.id, "version": asset.version}},
    )
    return ids


def site_box(asset_id: str, scale: float) -> Polygon:
    w, d, _ = ASSETS[asset_id].size_m
    return box(-w * scale / 2, -d * scale / 2, w * scale / 2, d * scale / 2)
