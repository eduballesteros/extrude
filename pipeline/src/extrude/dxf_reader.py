"""Lectura DXF → modelo intermedio en WCS.

- Las curvas (incluidas las de bloques) se convierten con `ezdxf.path`, que resuelve OCS,
  bulges, arcos y splines; se aplanan con una tolerancia expresada en metros.
- Los INSERT se expanden recursivamente con `virtual_entities()` (escala, rotación, OCS y
  anidamiento). Las entidades de un bloque en la capa "0" heredan la capa del INSERT.
- Nada se descarta en silencio: cada entidad se cuenta como leída y, si no se convierte,
  como no soportada (ACIS), omitida (tipos sin geometría útil) o fallida (con aviso).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

import numpy as np
from ezdxf import path as ezpath
from ezdxf import recover
from ezdxf.document import Drawing
from ezdxf.entities.dxfgfx import DXFGraphic
from ezdxf.entities.insert import Insert
from ezdxf.entities.mesh import Mesh
from ezdxf.entities.polygon import DXFPolygon
from ezdxf.entities.polyline import Polyface, Polyline, Polymesh
from ezdxf.enums import TextEntityAlignment
from ezdxf.lldxf.const import DXFStructureError
from ezdxf.math import Vec3
from ezdxf.render.mesh import MeshBuilder

from extrude.errors import InputFileError
from extrude.model import (
    INSUNITS_CODES,
    METERS_PER_UNIT,
    BBox,
    Feature,
    HatchFeature,
    InsertRef,
    LengthUnit,
    MeshFeature,
    PointFeature,
    Points,
    PolylineFeature,
    Provenance,
    ReadResult,
    TextFeature,
)

DEFAULT_FLATTEN_TOLERANCE_M = 0.01
MAX_FAILURE_WARNINGS = 20
# Vértice repetido (cierre de un contorno, 3DFACE triangular): en el DXF es una copia exacta.
# Tolerancia ABSOLUTA en unidades del dibujo: la relativa de np.allclose (1e-5) vale ~5 m en
# coordenadas UTM y fundía vértices reales (una cara de 3 m perdía la mitad de su superficie).
COINCIDENT_EPS = 1e-9

UNSUPPORTED_ACIS = frozenset(
    {
        "3DSOLID",
        "BODY",
        "REGION",
        "SURFACE",
        "EXTRUDEDSURFACE",
        "LOFTEDSURFACE",
        "REVOLVEDSURFACE",
        "SWEPTSURFACE",
        "PLANESURFACE",
    }
)
CURVE_TYPES = frozenset({"LWPOLYLINE", "LINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"})
FACE_TYPES = frozenset({"3DFACE", "SOLID", "TRACE"})
BLOCK_LAYER = "0"


@dataclass(frozen=True, slots=True)
class _Context:
    """Estado heredado al descender en INSERT anidados."""

    parent_layer: str | None = None
    block_path: tuple[str, ...] = ()
    attributes: tuple[tuple[str, str], ...] = ()
    insert: InsertRef | None = None  # INSERT de primer nivel (para instancing)


def read_dxf(
    source: Path,
    *,
    units_override: LengthUnit | None = None,
    flatten_tolerance_m: float = DEFAULT_FLATTEN_TOLERANCE_M,
) -> ReadResult:
    doc, load_messages = _load(source)
    insunits = int(doc.header.get("$INSUNITS", 0))
    units, assumed = _resolve_units(insunits, units_override)
    result = ReadResult(
        source=source,
        dxf_version=doc.acad_release,
        insunits_code=insunits,
        units=units,
        units_assumed=assumed,
        layers=sorted(layer.dxf.name for layer in doc.layers),
        warnings=load_messages,
    )
    if assumed:
        result.warnings.append(
            f"$INSUNITS = {insunits} (sin unidades o no soportado): se asumen metros. "
            "Si no es correcto, fuerza las unidades con --units mm|cm|m."
        )
    tolerance_du = flatten_tolerance_m / METERS_PER_UNIT[units]
    _Walker(result, tolerance_du).walk(doc.modelspace(), _Context())
    result.bbox = _bbox(result.features)
    return result


def _load(source: Path) -> tuple[Drawing, list[str]]:
    if not source.is_file():
        raise InputFileError(f"No existe el archivo '{source}'.", "Comprueba la ruta.")
    try:
        doc, auditor = recover.readfile(source)
    except DXFStructureError as exc:
        raise InputFileError(
            f"'{source.name}' no es un DXF válido o está dañado ({exc}).",
            "Vuelve a exportarlo desde AutoCAD/BricsCAD (SAVEAS → DXF 2018) tras AUDIT.",
        ) from exc
    except OSError as exc:
        raise InputFileError(f"No se puede leer '{source}': {exc}.", "Revisa permisos.") from exc
    messages = []
    if auditor.has_fixes:
        messages.append(f"Se repararon {len(auditor.fixes)} problemas estructurales del DXF.")
    if auditor.has_errors:
        details = "; ".join(e.message for e in auditor.errors[:5])
        messages.append(
            f"{len(auditor.errors)} errores estructurales sin reparar ({details}). Si falta "
            "geometría, ejecuta AUDIT/RECOVER en AutoCAD/BricsCAD y vuelve a exportar."
        )
    return doc, messages


def _resolve_units(code: int, override: LengthUnit | None) -> tuple[LengthUnit, bool]:
    if override is not None:
        return override, False
    if code in INSUNITS_CODES:
        return INSUNITS_CODES[code], False
    return LengthUnit.M, True


def _bbox(features: list[Feature]) -> BBox | None:
    arrays: list[Points] = []
    for f in features:
        match f:
            case PolylineFeature(points=p) | MeshFeature(vertices=p):
                arrays.append(p)
            case HatchFeature(loops=loops):
                arrays.extend(loops)
            case TextFeature(position=p) | PointFeature(position=p):
                arrays.append(p.reshape(1, 3))
    return BBox.of(np.vstack(arrays)) if arrays else None


def _same_point(a: Points, b: Points) -> bool:
    return bool(np.allclose(a, b, rtol=0.0, atol=COINCIDENT_EPS))


def _to_array(vertices: Iterable[Vec3]) -> Points:
    return np.array([(v.x, v.y, v.z) for v in vertices], dtype=np.float64).reshape(-1, 3)


class _Walker:
    def __init__(self, result: ReadResult, tolerance: float) -> None:
        self.result = result
        self.tolerance = tolerance
        self.failures = 0

    def walk(self, entities: Iterable[DXFGraphic], ctx: _Context) -> None:
        for entity in entities:
            dxftype = entity.dxftype()
            layer = self._layer(entity, ctx)
            self.result.read[(layer, dxftype)] += 1
            try:
                self._dispatch(entity, dxftype, layer, ctx)
            except Exception as exc:  # una entidad corrupta no aborta la lectura; se reporta
                self._fail(layer, dxftype, entity, exc)

    def _layer(self, entity: DXFGraphic, ctx: _Context) -> str:
        layer = str(entity.dxf.get("layer", BLOCK_LAYER))
        if layer == BLOCK_LAYER and ctx.parent_layer is not None:
            return ctx.parent_layer
        return layer

    def _source(self, entity: DXFGraphic, ctx: _Context) -> Provenance:
        return Provenance(
            dxftype=entity.dxftype(),
            handle=entity.dxf.get("handle"),
            block_path=ctx.block_path,
            attributes=ctx.attributes,
            insert=ctx.insert,
        )

    def _emit(self, feature: Feature) -> None:
        self.result.features.append(feature)

    def _fail(self, layer: str, dxftype: str, entity: DXFGraphic, exc: Exception) -> None:
        self.result.failed[(layer, dxftype)] += 1
        self.failures += 1
        if self.failures <= MAX_FAILURE_WARNINGS:
            handle = entity.dxf.get("handle") or "virtual"
            self.result.warnings.append(
                f"No se pudo leer {dxftype} (handle {handle}, capa {layer}): {exc}"
            )

    def _dispatch(self, entity: DXFGraphic, dxftype: str, layer: str, ctx: _Context) -> None:
        if dxftype == "INSERT":
            self._insert(entity, layer, ctx)  # type: ignore[arg-type]
        elif dxftype in CURVE_TYPES:
            self._curve(entity, layer, ctx)
        elif dxftype == "POLYLINE":
            self._polyline(cast(Polyline, entity), layer, ctx)
        elif dxftype == "HATCH":
            self._hatch(cast(DXFPolygon, entity), layer, ctx)
        elif dxftype in FACE_TYPES:
            self._face(entity, layer, ctx)
        elif dxftype == "MESH":
            self._mesh(MeshBuilder.from_mesh(cast(Mesh, entity)), entity, layer, ctx)
        elif dxftype in ("TEXT", "MTEXT"):
            self._text(entity, layer, ctx)
        elif dxftype == "POINT":
            position = _to_array([Vec3(entity.dxf.location)])[0]
            self._emit(PointFeature(layer, position, self._source(entity, ctx)))
        elif dxftype in UNSUPPORTED_ACIS:
            self.result.unsupported[(layer, dxftype)] += 1
        else:
            self.result.skipped[(layer, dxftype)] += 1

    def _insert(self, insert: Insert, layer: str, ctx: _Context) -> None:
        own_attribs = tuple((a.dxf.tag, a.dxf.text) for a in insert.attribs)
        child = _Context(
            parent_layer=layer,
            block_path=(*ctx.block_path, insert.dxf.name),
            attributes=ctx.attributes + own_attribs,
            insert=ctx.insert,
        )
        for attrib in insert.attribs:
            attrib_layer = self._layer(attrib, _Context(parent_layer=layer))
            position = _to_array([attrib.ocs().to_wcs(attrib.dxf.insert)])[0]
            self._emit(
                TextFeature(
                    attrib_layer,
                    attrib.dxf.text,
                    position,
                    self._source(attrib, child),
                    tag=attrib.dxf.tag,
                )
            )
        refs = list(insert.multi_insert()) if insert.mcount > 1 else [insert]
        for index, ref in enumerate(refs):
            ref_ctx = child
            if ctx.insert is None:  # primer nivel: cada copia (también de MINSERT) es una instancia
                handle = f"{insert.dxf.get('handle', '?')}#{index}"
                matrix = tuple(float(v) for v in ref.matrix44())
                ref_ctx = replace(child, insert=InsertRef(handle, insert.dxf.name, matrix))
            self.walk(ref.virtual_entities(), ref_ctx)

    def _curve(self, entity: DXFGraphic, layer: str, ctx: _Context) -> None:
        curve = ezpath.make_path(entity)
        points = _to_array(curve.flattening(self.tolerance))
        closed = curve.is_closed
        if closed and len(points) > 1:
            points = points[:-1]
        self._emit(PolylineFeature(layer, points, closed, self._source(entity, ctx)))

    def _polyline(self, entity: Polyline, layer: str, ctx: _Context) -> None:
        if entity.is_poly_face_mesh or entity.is_polygon_mesh:
            mesh = MeshBuilder.from_polyface(cast(Polyface | Polymesh, entity))
            self._mesh(mesh, entity, layer, ctx)
        else:
            self._curve(entity, layer, ctx)

    def _hatch(self, entity: DXFPolygon, layer: str, ctx: _Context) -> None:
        loops = []
        for boundary in ezpath.from_hatch(entity):
            points = _to_array(boundary.flattening(self.tolerance))
            if len(points) > 1 and _same_point(points[0], points[-1]):
                points = points[:-1]
            if len(points) >= 3:
                loops.append(points)
        self._emit(HatchFeature(layer, tuple(loops), self._source(entity, ctx)))

    def _face(self, entity: DXFGraphic, layer: str, ctx: _Context) -> None:
        vertices = _to_array(entity.wcs_vertices())  # type: ignore[attr-defined]
        if len(vertices) == 4 and _same_point(vertices[2], vertices[3]):
            vertices = vertices[:3]  # cuadrilátero que en realidad es triángulo
        face = tuple(range(len(vertices)))
        self._emit(MeshFeature(layer, vertices, (face,), self._source(entity, ctx)))

    def _mesh(self, mesh: MeshBuilder, entity: DXFGraphic, layer: str, ctx: _Context) -> None:
        vertices = _to_array(mesh.vertices)
        faces = tuple(tuple(int(i) for i in face) for face in mesh.faces)
        self._emit(MeshFeature(layer, vertices, faces, self._source(entity, ctx)))

    def _text(self, entity: DXFGraphic, layer: str, ctx: _Context) -> None:
        text: str = entity.plain_text()  # type: ignore[attr-defined]
        if entity.dxftype() == "MTEXT":
            wcs = Vec3(entity.dxf.insert)
        else:
            align, p1, p2 = entity.get_placement()  # type: ignore[attr-defined]
            two_points = align in (TextEntityAlignment.ALIGNED, TextEntityAlignment.FIT)
            ocs_point = p1.lerp(p2) if two_points and p2 is not None else p1
            wcs = entity.ocs().to_wcs(ocs_point)
        position = _to_array([wcs])[0]
        self._emit(TextFeature(layer, text.strip(), position, self._source(entity, ctx)))
