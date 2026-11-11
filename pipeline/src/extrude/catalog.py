"""Catálogo de objetos: identidad persistente, etiquetas de usuario y revisión geométrica.

- **ID** (`obj-<16 hex>`): hash de la capa y de la procedencia de cada entidad de origen (handle;
  para entidades virtuales de bloques, INSERT de primer nivel + ruta de bloques + tipo DXF). No
  depende del material, la tesela, la primitiva exportada, el orden de lectura ni de `_OBJECT_ID`.
  Si varios objetos comparten procedencia (un HATCH con varias islas, bloques anidados iguales),
  se desempatan por posición: `obj-…`, `obj-…-2`, `obj-…-3`.
- **Etiqueta** (E-001, V-001…): por tipo de capa, numerada de norte a sur y de oeste a este en el
  dibujo (norte = +Y del plano). Es solo un nombre amigable; la identidad es el ID.
- **Revisión geométrica** (`sha256:…`): hash de la geometría construida (vértices al micrómetro,
  caras y superficies de cada objeto, líneas y CRS). El mismo archivo con la misma configuración
  produce la misma revisión; cualquier cambio de forma, altura o apoyo produce otra.

Los IDs son estables dentro de una revisión. Tras editar el DWG no se garantiza que coincidan:
quien los use (materials.json) debe comprobar la revisión y avisar de las diferencias.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from extrude import __version__
from extrude.config import Kind
from extrude.extrusion import MeshData, triangle_areas
from extrude.model import Provenance
from extrude.surfaces import Surface

if TYPE_CHECKING:  # build importa este módulo para asignar los IDs
    from extrude.build import BuildResult, LayerGeometry, SceneObject

CATALOG_NAME = "objects.json"
CATALOG_SCHEMA_VERSION = 1
GEOMETRY_FORMAT = "extrude-geometry/1"
ID_PREFIX = "obj-"
ID_HEX_DIGITS = 16
HASH_RESOLUTION_M = 1e-6  # los vértices se cuantizan al micrómetro antes de calcular hashes
LABEL_PREFIX: Mapping[Kind, str] = {
    Kind.BUILDING: "E",
    Kind.ROAD: "V",
    Kind.WATER: "A",
    Kind.VEGETATION: "G",
    Kind.TERRAIN: "T",
}
NORTH_CONVENTION = {
    "axis": "+Y",
    "reference": "plan",
    "description": "Norte del plano (+Y del dibujo); no se corrige a norte geográfico.",
}


def source_key(source: Provenance) -> str:
    """Clave estable de una entidad de origen dentro del dibujo."""
    if source.insert is None and not source.block_path and source.handle:
        return f"h:{source.handle}"
    insert = source.insert.handle if source.insert else "-"
    path = "/".join(source.block_path)
    own = f"@{source.handle}" if source.handle else ""
    return f"i:{insert}:{path}:{source.dxftype}{own}"


def object_key(layer: str, obj: SceneObject) -> str:
    keys = sorted({source_key(s) for s in obj.sources})
    if not keys:
        keys = [f"t:{obj.source_type}"]
    return layer + "\x1f" + "\x1e".join(keys)


def _base_id(key: str) -> str:
    return ID_PREFIX + hashlib.sha256(key.encode("utf-8")).hexdigest()[:ID_HEX_DIGITS]


def _quantized(vertices: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
    return np.round(np.asarray(vertices, dtype=np.float64) / HASH_RESOLUTION_M).astype(np.int64)


def mesh_digest(mesh: MeshData) -> bytes:
    h = hashlib.sha256()
    h.update(_quantized(mesh.vertices).tobytes())
    h.update(np.ascontiguousarray(mesh.faces, dtype=np.int64).tobytes())
    h.update(mesh.face_surfaces.tobytes())
    return h.digest()


def _position_key(obj: SceneObject) -> tuple[Any, ...]:
    """Orden determinista para desempatar objetos con la misma procedencia."""
    v = obj.mesh.vertices
    if not len(v):
        return ((), (), 0, b"")
    lo, hi = v.min(axis=0), v.max(axis=0)
    center = tuple(int(x) for x in _quantized((lo + hi) / 2))
    return (
        center,
        tuple(int(x) for x in _quantized(lo)),
        len(obj.mesh.faces),
        mesh_digest(obj.mesh),
    )


def _center_xy(obj: SceneObject) -> tuple[float, float]:
    if obj.footprint is not None:
        c = obj.footprint.centroid
        return (float(c.x), float(c.y))
    v = obj.mesh.vertices
    if not len(v):
        return (0.0, 0.0)
    mid = (v[:, :2].min(axis=0) + v[:, :2].max(axis=0)) / 2
    return (float(mid[0]), float(mid[1]))


def assign_ids(result: BuildResult) -> None:
    """Asigna `object_id` y `label` a cada objeto de la escena (en sitio, determinista)."""
    groups: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for name, geometry in result.layers.items():
        for index, obj in enumerate(geometry.objects):
            groups[_base_id(object_key(name, obj))].append((name, index))

    ids: dict[tuple[str, int], str] = {}
    for base, members in groups.items():
        if len(members) == 1:
            ids[members[0]] = base
            continue
        ordered = sorted(members, key=lambda m: _position_key(result.layers[m[0]].objects[m[1]]))
        for rank, member in enumerate(ordered, start=1):
            ids[member] = base if rank == 1 else f"{base}-{rank}"

    labels = _labels(result.layers, ids)
    for name, geometry in result.layers.items():
        geometry.objects = [
            replace(obj, object_id=ids[(name, i)], label=labels.get(ids[(name, i)]))
            for i, obj in enumerate(geometry.objects)
        ]


def _labels(
    layers: Mapping[str, LayerGeometry], ids: Mapping[tuple[str, int], str]
) -> dict[str, str]:
    by_prefix: dict[str, list[tuple[float, float, str]]] = defaultdict(list)
    for name, geometry in layers.items():
        prefix = LABEL_PREFIX.get(geometry.rule.kind, "O")
        for i, obj in enumerate(geometry.objects):
            x, y = _center_xy(obj)
            # Norte a sur y oeste a este, al decímetro para que el ruido no reordene filas
            by_prefix[prefix].append((-round(y, 1), round(x, 1), ids[(name, i)]))
    labels: dict[str, str] = {}
    for prefix, items in by_prefix.items():
        width = max(3, len(str(len(items))))
        for n, (_, _, object_id) in enumerate(sorted(items), start=1):
            labels[object_id] = f"{prefix}-{n:0{width}d}"
    return labels


def geometry_revision(result: BuildResult, crs: str | None) -> str:
    h = hashlib.sha256()
    header = {"format": GEOMETRY_FORMAT, "crs": crs}
    h.update(json.dumps(header, sort_keys=True).encode("utf-8"))
    entries: list[tuple[str, str, str, bytes]] = []
    for name, geometry in result.layers.items():
        kind = geometry.rule.kind.value
        for obj in geometry.objects:
            entries.append((obj.object_id or "", name, kind, mesh_digest(obj.mesh)))
        for line in geometry.lines:
            raw = _quantized(line.points).tobytes() + bytes([line.closed])
            entries.append(("", name, "line", hashlib.sha256(raw).digest()))
    for object_id, layer, kind, digest in sorted(entries):
        h.update(f"{object_id}\x1f{layer}\x1f{kind}\x1f".encode())
        h.update(digest)
    return "sha256:" + h.hexdigest()


def surface_summary(mesh: MeshData) -> dict[str, dict[str, float | int]]:
    """Superficies presentes en el objeto: nº de triángulos y área (m²)."""
    if not mesh.triangle_count:
        return {}
    areas = triangle_areas(mesh.vertices, mesh.faces)
    surfaces = mesh.face_surfaces
    summary: dict[str, dict[str, float | int]] = {}
    for code in np.unique(surfaces):
        mask = surfaces == code
        summary[Surface(int(code)).key] = {
            "triangles": int(mask.sum()),
            "areaM2": round(float(areas[mask].sum()), 3),
        }
    return summary


def _r(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(float(value), digits)


def _entry(name: str, kind: Kind, obj: SceneObject) -> dict[str, Any]:
    v = obj.mesh.vertices
    insert = obj.source_insert or obj.instance
    block_paths = sorted({"/".join(s.block_path) for s in obj.sources if s.block_path})
    attributes: dict[str, str] = {}
    for source in obj.sources:
        for tag, value in source.attributes:
            attributes.setdefault(tag, value)
    x, y = _center_xy(obj)
    return {
        "id": obj.object_id,
        "label": obj.label,
        "layer": name,
        "kind": kind.value,
        "sourceType": obj.source_type,
        "provenance": {
            "handles": list(obj.handles),
            "insert": (
                {
                    "handle": insert.handle.partition("#")[0],
                    "instance": insert.handle,
                    "block": insert.block,
                }
                if insert
                else None
            ),
            "blockPaths": block_paths,
            "attributes": attributes,
        },
        "heightM": _r(obj.height),
        "floorM": _r(obj.floor_elevation),
        "foundationHeightM": _r(obj.foundation_height),
        "centerM": [round(x, 3), round(y, 3)],
        "bboxM": (
            {
                "min": [round(float(c), 3) for c in v.min(axis=0)],
                "max": [round(float(c), 3) for c in v.max(axis=0)],
            }
            if len(v)
            else None
        ),
        "footprintAreaM2": _r(obj.footprint.area) if obj.footprint is not None else None,
        "surfaces": surface_summary(obj.mesh),
        "export": None,
    }


def build_catalog(
    result: BuildResult,
    *,
    revision: str,
    source_units: str,
    crs: str | None,
    origin: Sequence[float],
    export_refs: Mapping[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Documento `objects.json`. Coordenadas en metros, ejes del dibujo (Z-up, sin recentrar):
    el norte y las referencias espaciales se resuelven aquí, antes del cambio de ejes."""
    objects = []
    for name, geometry in result.layers.items():
        for obj in geometry.objects:
            entry = _entry(name, geometry.rule.kind, obj)
            if (
                export_refs is not None
                and obj.object_id is not None
                and obj.object_id in export_refs
            ):
                entry["export"] = export_refs[obj.object_id]
            objects.append(entry)
    objects.sort(key=lambda e: (e["label"] or "", e["id"] or ""))
    return {
        "schemaVersion": CATALOG_SCHEMA_VERSION,
        "generator": f"extrude {__version__}",
        "geometryRevision": revision,
        "units": "m",
        "sourceUnits": source_units,
        "crs": crs,
        "originOffset": [float(c) for c in origin],
        "coordinates": "Ejes del dibujo (Z arriba), metros, antes del recentrado",
        "north": NORTH_CONVENTION,
        "surfaces": [s.key for s in Surface],
        "objects": objects,
    }


def export_refs(layers: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """Dónde acaba cada ID en un GLB único: nodo de capa + índice, o nodo de bloque + instancia.
    `layers` son `gltf_export.ExportLayer` (sin importarlo, para evitar el ciclo)."""
    refs: dict[str, dict[str, Any]] = {}
    for layer in layers:
        for index, obj in enumerate(layer.objects):
            if obj.object_id:
                refs[obj.object_id] = {"node": layer.name, "objectIndex": index}
        for group in layer.groups:
            for instance, ids in enumerate(group.object_ids):
                for part, object_id in enumerate(ids):
                    if object_id:
                        refs[object_id] = {
                            "node": f"{layer.name}/{group.block}",
                            "instance": instance,
                            "part": part,
                        }
    return refs


def write_catalog(catalog: Mapping[str, Any], path: Path) -> None:
    # Compacto: con decenas de miles de objetos, la sangría duplica el tamaño
    text = json.dumps(catalog, separators=(",", ":"), ensure_ascii=False)
    path.write_text(text, encoding="utf-8")


def load_catalog(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data
