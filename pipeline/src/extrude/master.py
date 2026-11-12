"""Maestro editable de un proyecto: GLB comprimidos y proyectos teselados.

Las ediciones (materiales, IA, deshacer/rehacer) trabajan siempre sobre un GLB SIN comprimir y de
una sola pieza, el maestro. Lo que se entrega se deriva de él:

- GLB comprimido (Draco/Meshopt) de entrada: se decodifica con Node (tools/decompress.mjs:
  atributos en float, sin cuantizar y sin extensiones de compresión) a `model.master.glb`. El
  original queda intacto como rev-000 descargable. Exportar comprimido es opcional y se hace
  desde el estado actual (`export_compressed`).
- Proyecto teselado (`convert --tiles`): convert escribe también el maestro completo. Cada
  revisión confirmada (y cada deshacer/rehacer) regenera las teselas desde el GLB vigente con la
  MISMA regla de reparto que convert (centro del objeto, origen y tamaño de tesela del
  manifest) y reescribe `manifest.json`; las teselas vacías salen del manifest, pero los archivos
  anteriores se conservan para los manifests ya descargados.
"""

from __future__ import annotations

import json
import math
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from extrude.errors import ExtrudeError
from extrude.glb_edit import COMPONENTS, WIDTH, GlbEditor, instance_numbers, quat_rotate
from extrude.gltf_export import INSTANCING_EXT, decode_glb, encode_glb

BASE_GLB = "model.glb"
MASTER_GLB = "model.master.glb"
MANIFEST = "manifest.json"
TILES_DIR = "tiles"
EXPORTS_DIR = "exports"
COMPRESSION_EXTENSIONS = {
    "KHR_draco_mesh_compression",
    "EXT_meshopt_compression",
    "KHR_mesh_quantization",
}


def glb_extensions(path: Path) -> set[str]:
    doc, _ = decode_glb(path.read_bytes())
    return set(doc.get("extensionsUsed", [])) | set(doc.get("extensionsRequired", []))


def is_compressed(path: Path) -> bool:
    return is_compressed_glb(path.read_bytes())


def is_compressed_glb(data: bytes) -> bool:
    try:
        doc, _ = decode_glb(data)
    except (ValueError, KeyError, IndexError):
        return False  # inspect_base dará el error de formato
    used = set(doc.get("extensionsUsed", [])) | set(doc.get("extensionsRequired", []))
    return bool(used & COMPRESSION_EXTENSIONS)


def pack_views(data: bytes) -> bytes:
    """Reescribe los accesores con `byteStride` (gltf-transform lo escribe siempre en vértices) en
    vistas compactas sin intercalar, que es lo que leen GlbEditor y materialize."""
    doc, blob = decode_glb(data)
    out = bytearray(blob)
    changed = False
    for acc in doc.get("accessors", []):
        if "bufferView" not in acc:
            continue
        view = doc["bufferViews"][acc["bufferView"]]
        stride = view.get("byteStride")
        if not stride:
            continue
        dtype = np.dtype(COMPONENTS[acc["componentType"]])
        width = WIDTH[acc["type"]]
        start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        values = np.ndarray(
            (acc["count"], width),
            dtype=dtype,
            buffer=bytes(blob),
            offset=start,
            strides=(stride, dtype.itemsize),
        )
        packed = np.ascontiguousarray(values)
        out += b"\x00" * (-len(out) % 4)
        new_view = {"buffer": 0, "byteOffset": len(out), "byteLength": packed.nbytes}
        if "target" in view:
            new_view["target"] = view["target"]
        out += packed.tobytes()
        doc["bufferViews"].append(new_view)
        acc["bufferView"] = len(doc["bufferViews"]) - 1
        acc["byteOffset"] = 0
        changed = True
    if not changed:
        return data
    doc["buffers"] = [{"byteLength": len(out) + (-len(out) % 4)}]
    return GlbEditor(encode_glb(doc, bytes(out))).encode()  # compacta: quita las vistas viejas


def decompress_to_master(out_dir: Path, source: Path | None = None) -> Path:
    """Decodifica `source` (por defecto model.glb) a model.master.glb sin compresión."""
    from extrude.node_tools import decompress

    source = source or out_dir / BASE_GLB
    target = out_dir / MASTER_GLB
    with tempfile.TemporaryDirectory(prefix="extrude-dec-") as work:
        raw = Path(work) / "decoded.glb"
        decompress(source, raw)
        data = pack_views(raw.read_bytes())
    doc, _ = decode_glb(data)
    left = set(doc.get("extensionsUsed", [])) & COMPRESSION_EXTENSIONS
    if left:
        raise ExtrudeError(
            f"El GLB sigue comprimido tras decodificarlo ({', '.join(sorted(left))}).",
            "Actualiza tools/ con `npm install`.",
        )
    partial = target.with_suffix(".partial")
    partial.write_bytes(data)
    partial.replace(target)
    return target


def editable_base(out_dir: Path) -> Path:
    """GLB sin comprimir y completo sobre el que se edita (rev-000 de un proyecto nuevo)."""
    master = out_dir / MASTER_GLB
    if master.is_file():
        return master
    base = out_dir / BASE_GLB
    if base.is_file():
        return decompress_to_master(out_dir) if is_compressed(base) else base
    if (out_dir / MANIFEST).is_file():
        raise ExtrudeError(
            "Este proyecto teselado no tiene GLB maestro (se convirtió con una versión anterior).",
            "Vuelve a convertir el plano con --tiles: ahora se guarda también el maestro editable.",
        )
    raise ExtrudeError("El trabajo no tiene GLB.", "Convierte el plano de nuevo.")


# --- Exportación comprimida ------------------------------------------------------------------


def export_compressed(out_dir: Path, source: Path, method: str, revision: str) -> Path:
    """GLB comprimido del estado actual (se guarda por revisión: no se recomprime dos veces)."""
    from extrude.node_tools import Compression, compress

    kind = Compression(method)
    target = out_dir / EXPORTS_DIR / f"{revision}.{kind.value}.glb"
    if not target.is_file():
        target.parent.mkdir(exist_ok=True)
        partial = target.with_suffix(".partial.glb")
        compress(source, partial, kind)
        partial.replace(target)
    return target


# --- Teselas ---------------------------------------------------------------------------------


def _world_bounds(editor: GlbEditor, node: dict[str, Any]) -> tuple[NDArray[Any], NDArray[Any]]:
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    if "mesh" not in node:
        return lo, hi
    prims = editor.doc["meshes"][node["mesh"]]["primitives"]
    pts = [editor.read(p["attributes"]["POSITION"]) for p in prims if "POSITION" in p["attributes"]]
    if not pts:
        return lo, hi
    local = np.concatenate(pts).astype(np.float64)
    inst = node.get("extensions", {}).get(INSTANCING_EXT)
    if inst is None:
        return local.min(axis=0), local.max(axis=0)
    a = inst["attributes"]
    corners = np.array(
        [
            [x, y, z]
            for x in local[:, 0][[local[:, 0].argmin(), local[:, 0].argmax()]]
            for y in local[:, 1][[local[:, 1].argmin(), local[:, 1].argmax()]]
            for z in local[:, 2][[local[:, 2].argmin(), local[:, 2].argmax()]]
        ]
    )
    t, r, s = editor.read(a["TRANSLATION"]), editor.read(a["ROTATION"]), editor.read(a["SCALE"])
    for ti, ri, si in zip(t, r, s, strict=True):
        world = quat_rotate(ri, corners * si) + ti
        lo, hi = np.minimum(lo, world.min(axis=0)), np.maximum(hi, world.max(axis=0))
    return lo, hi


def _subset(data: bytes, keep: set[str], keep_line: Any) -> tuple[bytes, dict[str, Any], int]:
    """Copia del GLB con solo los objetos de `keep` (y los tramos de línea que `keep_line`
    acepta). Devuelve el GLB, su bbox y sus triángulos."""
    editor = GlbEditor(data)
    doc = editor.doc
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    triangles = 0
    for node in doc["nodes"]:
        extras = node.get("extras", {})
        if "mesh" not in node:
            continue
        inst = node.get("extensions", {}).get(INSTANCING_EXT)
        if inst is not None:
            groups = extras.get("object_ids", [])
            picked = [n for n, ids in enumerate(groups) if any(i in keep for i in ids if i)]
            numbers = instance_numbers(extras)
            for key in ("TRANSLATION", "ROTATION", "SCALE"):
                values = editor.read(inst["attributes"][key])
                if picked:
                    editor.write(inst["attributes"][key], values[picked])
            if not picked:
                node.pop("mesh")
                node.pop("extensions")
                continue
            extras["object_ids"] = [groups[n] for n in picked]
            extras["numbers"] = [numbers[n] for n in picked]
            extras["instances"] = len(picked)
        else:
            table = extras.get("objects", {}).get("id")
            mesh = doc["meshes"][node["mesh"]]
            kept_prims = []
            for prim in mesh["primitives"]:
                attrs = prim["attributes"]
                if "_OBJECT_ID" in attrs and table is not None:
                    rows = np.rint(editor.read(attrs["_OBJECT_ID"])).astype(int)
                    keep_rows = np.array([bool(i) and i in keep for i in table] + [False])
                    vertex_keep = keep_rows[np.clip(rows, 0, len(table))]
                elif prim.get("mode") == 1 and "indices" in prim:  # líneas: por primer vértice
                    pos = editor.read(attrs["POSITION"])
                    segments = editor.read(prim["indices"]).reshape(-1, 2)
                    chosen = segments[[keep_line(pos[a]) for a, _ in segments]]
                    if not len(chosen):
                        continue
                    editor.write(prim["indices"], chosen.reshape(-1).astype(np.uint32), 5125)
                    kept_prims.append(prim)
                    continue
                else:
                    kept_prims.append(prim)
                    continue
                if not vertex_keep.any():
                    continue
                remap = np.cumsum(vertex_keep) - 1
                if "indices" in prim:
                    faces = editor.read(prim["indices"]).reshape(-1, 3).astype(np.int64)
                    faces = faces[vertex_keep[faces].all(axis=1)]
                    if not len(faces):
                        continue
                    editor.write(prim["indices"], remap[faces].reshape(-1).astype(np.uint32), 5125)
                for accessor in attrs.values():
                    editor.write(accessor, editor.read(accessor)[vertex_keep])
                kept_prims.append(prim)
            if table is not None:
                extras["objects"]["id"] = [i if i in keep else None for i in table]
            if not kept_prims:
                node.pop("mesh")
                continue
            mesh["primitives"] = kept_prims
        b = _world_bounds(editor, node)
        lo, hi = np.minimum(lo, b[0]), np.maximum(hi, b[1])
        for prim in doc["meshes"][node["mesh"]]["primitives"]:
            if prim.get("mode", 4) == 4 and "indices" in prim:
                count = doc["accessors"][prim["indices"]]["count"] // 3
                triangles += count * max(1, int(extras.get("instances", 1)))
    bbox = {"min": lo.tolist(), "max": hi.tolist()} if np.isfinite(lo).all() else None
    scene = doc["scenes"][doc.get("scene", 0)]
    scene.setdefault("extras", {})["bbox"] = bbox
    # Mallas sin usar (sus accesores se quedan, pero `encode` solo compacta vistas): se retiran
    used = {n["mesh"] for n in doc["nodes"] if "mesh" in n}
    mesh_remap = {old: new for new, old in enumerate(sorted(used))}
    doc["meshes"] = [m for i, m in enumerate(doc["meshes"]) if i in used]
    for n in doc["nodes"]:
        if "mesh" in n:
            n["mesh"] = mesh_remap[n["mesh"]]
    _drop_unused_accessors(doc)
    if not any(INSTANCING_EXT in n.get("extensions", {}) for n in doc["nodes"]):
        doc["extensionsUsed"] = [e for e in doc.get("extensionsUsed", []) if e != INSTANCING_EXT]
        if not doc["extensionsUsed"]:
            doc.pop("extensionsUsed")
    return editor.encode(), bbox or {}, triangles


def _drop_unused_accessors(doc: dict[str, Any]) -> None:
    used: set[int] = set()
    for mesh in doc["meshes"]:
        for prim in mesh["primitives"]:
            used |= set(prim["attributes"].values())
            if "indices" in prim:
                used.add(prim["indices"])
    for node in doc["nodes"]:
        used |= set(
            node.get("extensions", {}).get(INSTANCING_EXT, {}).get("attributes", {}).values()
        )
    for skin in doc.get("skins", []):
        if "inverseBindMatrices" in skin:
            used.add(skin["inverseBindMatrices"])
    order = sorted(used)
    remap = {old: new for new, old in enumerate(order)}
    doc["accessors"] = [doc["accessors"][i] for i in order]
    for mesh in doc["meshes"]:
        for prim in mesh["primitives"]:
            prim["attributes"] = {k: remap[v] for k, v in prim["attributes"].items()}
            if "indices" in prim:
                prim["indices"] = remap[prim["indices"]]
    for node in doc["nodes"]:
        attrs = node.get("extensions", {}).get(INSTANCING_EXT, {}).get("attributes")
        if attrs:
            for k, v in attrs.items():
                attrs[k] = remap[v]


def _object_centers(data: bytes) -> tuple[dict[str, NDArray[Any]], list[float]]:
    """Centro en planta (ejes del dibujo, ABSOLUTOS) de cada objeto, como convert._center."""
    from extrude.glb_edit import locate, object_center

    editor = GlbEditor(data)
    origin = editor.doc["scenes"][0].get("extras", {}).get("origin_offset") or [0.0, 0.0, 0.0]
    centers = {}
    for object_id, where in locate(editor.doc).items():
        if where.kind == "instance" and where.instance is not None:
            # Como convert: una copia de bloque va a la tesela de su punto de inserción
            node = editor.doc["nodes"][where.node]
            attrs = node["extensions"][INSTANCING_EXT]["attributes"]
            g = editor.read(attrs["TRANSLATION"])[where.instance].astype(np.float64)
        else:
            g = object_center(editor, where)
        # glTF (x, y arriba, z) → dibujo (x, -z), más el origen recentrado
        centers[object_id] = np.array([g[0] + origin[0], -g[2] + origin[1]])
    return centers, [float(v) for v in origin]


def regenerate_tiles(out_dir: Path, source: Path, revision: str) -> dict[str, Any] | None:
    """Reescribe tiles/*.glb y manifest.json desde `source` (GLB vigente). None si no es un
    proyecto teselado."""
    manifest_path = out_dir / MANIFEST
    if not manifest_path.is_file():
        return None
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    size = float(manifest["tile_size"])
    tile_origin = manifest.get("tile_origin")
    if tile_origin is None:
        raise ExtrudeError(
            "El manifest no tiene el origen de las teselas (versión anterior).",
            "Vuelve a convertir con --tiles.",
        )
    x0, y0 = float(tile_origin[0]), float(tile_origin[1])
    data = source.read_bytes()
    centers, origin = _object_centers(data)

    def key(x: float, y: float) -> tuple[int, int]:
        return (math.floor((x - x0) / size), math.floor((y - y0) / size))

    by_tile: dict[tuple[int, int], set[str]] = defaultdict(set)
    for object_id, c in centers.items():
        by_tile[key(float(c[0]), float(c[1]))].add(object_id)

    def line_tile(point: NDArray[Any]) -> tuple[int, int]:
        # glTF (x, y, z) → dibujo (x, -z) + origen
        return key(float(point[0]) + origin[0], float(-point[2]) + origin[1])

    line_tiles = _line_tiles(data, line_tile)
    method = manifest.get("compression")
    tiles_dir = out_dir / TILES_DIR
    tiles_dir.mkdir(exist_ok=True)
    entries = []
    written = set()
    for tile in sorted(set(by_tile) | line_tiles):
        keep = by_tile.get(tile, set())
        glb, bbox, triangles = _subset(data, keep, lambda p, t=tile: line_tile(p) == t)
        name = f"tile_{tile[0]}_{tile[1]}.{revision}.glb"
        target = tiles_dir / name
        partial = tiles_dir / f"{name}.partial"
        partial.write_bytes(glb)
        if method:
            from extrude.node_tools import Compression, compress

            compressed = tiles_dir / f"{name}.c.glb"
            compress(partial, compressed, Compression(method))
            partial.unlink()
            compressed.replace(target)
        else:
            partial.replace(target)
        written.add(name)
        entries.append(
            {
                "id": f"{tile[0]}_{tile[1]}",
                "file": f"{TILES_DIR}/{name}",
                "bbox": bbox,
                "triangles": triangles,
                "size_bytes": target.stat().st_size,
                "objects": len(keep),
            }
        )
    for stale in tiles_dir.glob("tile_*.glb"):
        if stale.name.endswith(f".{revision}.glb") and stale.name not in written:
            stale.unlink()
    manifest["tiles"] = entries
    manifest["revision"] = revision
    manifest["master"] = source.name
    doc, _ = decode_glb(data)
    extras = doc["scenes"][doc.get("scene", 0)].get("extras", {})
    manifest["geometry_revision"] = extras.get("geometry_revision")
    partial_manifest = manifest_path.with_suffix(".partial")
    partial_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    partial_manifest.replace(manifest_path)
    return manifest


def _line_tiles(data: bytes, line_tile: Any) -> set[tuple[int, int]]:
    editor = GlbEditor(data)
    found: set[tuple[int, int]] = set()
    for mesh in editor.doc.get("meshes", []):
        for prim in mesh["primitives"]:
            if prim.get("mode") == 1 and "indices" in prim:
                pos = editor.read(prim["attributes"]["POSITION"])
                for a in editor.read(prim["indices"]).reshape(-1, 2)[:, 0]:
                    found.add(line_tile(pos[a]))
    return found
