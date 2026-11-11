"""Contornos 2D → huellas (polígonos con huecos) listas para extruir.

Trabaja en metros, en el plano XY de WCS. Pasos:
  1. Polilíneas cerradas → anillos. Abiertas con extremos a ≤ tolerancia → se cierran.
  2. El resto (LINE, ARC, polilíneas abiertas) se ajusta en sus extremos y se poligoniza.
  3. Cada anillo se valida (`make_valid`); los de área ≈ 0 se descartan; se eliminan duplicados.
  4. Huecos por contención par/impar: un anillo dentro de otro es un patio; dentro de un
     patio vuelve a ser macizo.
  5. Orientación: exterior CCW, huecos CW.
Todo lo reparado o descartado se cuenta en `TopologyStats`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, fields

import numpy as np
import shapely
from numpy.typing import NDArray
from scipy.spatial import cKDTree
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.ops import polygonize_full, unary_union

from extrude.model import HatchFeature, PolylineFeature, Provenance

MIN_RING_POINTS = 3


@dataclass(frozen=True, slots=True)
class Footprint:
    polygon: Polygon  # metros; exterior CCW, huecos CW
    elevation: float  # Z media del contorno exterior de origen (metros)
    sources: tuple[Provenance, ...]


@dataclass
class TopologyStats:
    closed_by_tolerance: int = 0
    loops_from_segments: int = 0
    dangling_segments: int = 0
    duplicates_removed: int = 0
    repaired: int = 0
    degenerate_dropped: int = 0
    holes: int = 0

    def add(self, other: TopologyStats) -> None:
        for f in fields(self):
            setattr(self, f.name, getattr(self, f.name) + getattr(other, f.name))


@dataclass(frozen=True, slots=True)
class _Ring:
    polygon: Polygon  # solo exterior
    elevation: float
    sources: tuple[Provenance, ...]


def build_footprints(
    polylines: Sequence[PolylineFeature],
    hatches: Sequence[HatchFeature],
    *,
    scale: float,
    tolerance: float,
    detect_holes: bool = True,
    separate_elevations: bool = False,
) -> tuple[list[Footprint], TopologyStats]:
    stats = TopologyStats()
    if separate_elevations:
        # La topología XY solo es comparable dentro de una misma cota cuando Z determina
        # la base o altura del volumen. Separar ANTES de poligonizar también evita fusionar
        # segmentos superpuestos y promediar sus Z; los patios tampoco cruzan niveles.
        levels: dict[int, tuple[list[PolylineFeature], list[HatchFeature]]] = defaultdict(
            lambda: ([], [])
        )
        for line in polylines:
            level = round(_mean_elevation([line.points]) * scale / tolerance)
            levels[level][0].append(line)
        for hatch in hatches:
            level = round(_mean_elevation(hatch.loops) * scale / tolerance)
            levels[level][1].append(hatch)
        all_footprints = []
        for level_lines, level_hatches in levels.values():
            footprints, level_stats = build_footprints(
                level_lines,
                level_hatches,
                scale=scale,
                tolerance=tolerance,
                detect_holes=detect_holes,
            )
            all_footprints.extend(footprints)
            stats.add(level_stats)
        return all_footprints, stats
    min_area = tolerance * tolerance
    rings: list[_Ring] = []
    segments: list[PolylineFeature] = []

    for feature in polylines:
        points = feature.points * scale
        if not feature.closed:
            if _closes_within(points, tolerance):
                points = points[:-1]
                stats.closed_by_tolerance += 1
            else:
                segments.append(feature)
                continue
        rings.extend(_valid_rings(points, (feature.source,), min_area, stats))

    rings.extend(_rings_from_segments(segments, scale, tolerance, min_area, stats))
    rings = _deduplicate(rings, tolerance, stats)

    footprints = _assemble(rings, stats) if detect_holes else [_solid(r) for r in rings]
    # Un HATCH que rellena exactamente un contorno ya dibujado (muy habitual) no es otro sólido
    seen = {_key(f.polygon, tolerance) for f in footprints}
    for hatch in hatches:
        hatch_rings: list[_Ring] = []
        for loop in hatch.loops:
            hatch_rings.extend(_valid_rings(loop * scale, (hatch.source,), min_area, stats))
        for footprint in _assemble(hatch_rings, stats):
            key = _key(footprint.polygon, tolerance)
            if key in seen:
                stats.duplicates_removed += 1
                continue
            seen.add(key)
            footprints.append(footprint)
    return footprints, stats


def _closes_within(points: NDArray[np.float64], tolerance: float) -> bool:
    return len(points) > MIN_RING_POINTS and bool(
        np.linalg.norm(points[0, :2] - points[-1, :2]) <= tolerance
    )


def _valid_rings(
    points: NDArray[np.float64],
    sources: tuple[Provenance, ...],
    min_area: float,
    stats: TopologyStats,
) -> list[_Ring]:
    elevation = float(points[:, 2].mean()) if points.shape[1] == 3 else 0.0
    if len(points) < MIN_RING_POINTS:
        stats.degenerate_dropped += 1
        return []
    polygon = Polygon(points[:, :2])
    parts: list[Polygon] = [polygon]
    if not polygon.is_valid:
        parts = _polygons_in(shapely.make_valid(polygon))
        if parts:
            stats.repaired += 1
    kept = [Polygon(p.exterior) for p in parts if p.area >= min_area]
    if not kept:
        stats.degenerate_dropped += 1
    return [_Ring(p, elevation, sources) for p in kept]


def _polygons_in(geometry: BaseGeometry) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    if hasattr(geometry, "geoms"):
        return [p for g in geometry.geoms for p in _polygons_in(g)]
    return []


def _rings_from_segments(
    segments: Sequence[PolylineFeature],
    scale: float,
    tolerance: float,
    min_area: float,
    stats: TopologyStats,
) -> list[_Ring]:
    if not segments:
        return []
    kept = [f for f in segments if len(f.points) >= 2]  # índices de `lines` = índices de `kept`
    if not kept:
        return []
    coords = [f.points * scale for f in kept]
    _snap_endpoints(coords, tolerance)
    lines = [LineString(c[:, :2]) for c in coords]
    polygons, _cuts, dangles, _invalid = polygonize_full(unary_union(lines))
    stats.dangling_segments += len(getattr(dangles, "geoms", []))
    tree = shapely.STRtree(lines)
    rings: list[_Ring] = []
    for polygon in getattr(polygons, "geoms", []):
        if polygon.area < min_area:
            stats.degenerate_dropped += 1
            continue
        stats.loops_from_segments += 1
        near = tree.query(polygon.exterior, predicate="dwithin", distance=tolerance)
        sources = tuple(kept[int(i)].source for i in near)
        elevation = _mean_elevation([coords[int(i)] for i in near])
        rings.append(_Ring(Polygon(polygon.exterior), elevation, sources))
    return rings


def _mean_elevation(coords: Sequence[NDArray[np.float64]]) -> float:
    """Z media de los segmentos que forman un anillo; 0.0 si no hay datos 3D."""
    z = [c[:, 2] for c in coords if c.ndim == 2 and c.shape[1] >= 3 and len(c)]
    return float(np.concatenate(z).mean()) if z else 0.0


def _snap_endpoints(coords: list[NDArray[np.float64]], tolerance: float) -> None:
    """Une en un mismo punto los extremos que distan ≤ tolerancia (in situ)."""
    ends = np.array([c[i, :2] for c in coords for i in (0, -1)])
    parent = list(range(len(ends)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in cKDTree(ends).query_pairs(tolerance):
        parent[find(a)] = find(b)
    roots = np.array([find(i) for i in range(len(ends))])
    for root in np.unique(roots):
        members = np.flatnonzero(roots == root)
        if len(members) < 2:
            continue
        target = ends[members].mean(axis=0)
        for m in members:
            coords[m // 2][0 if m % 2 == 0 else -1, :2] = target


def _key(polygon: Polygon, tolerance: float) -> bytes:
    """Huella geométrica independiente del sentido y del vértice inicial."""
    key: bytes = shapely.normalize(shapely.set_precision(polygon, tolerance)).wkb
    return key


def _deduplicate(rings: list[_Ring], tolerance: float, stats: TopologyStats) -> list[_Ring]:
    seen: set[bytes] = set()
    unique: list[_Ring] = []
    for ring in rings:
        key = _key(ring.polygon, tolerance)
        if key in seen:
            stats.duplicates_removed += 1
            continue
        seen.add(key)
        unique.append(ring)
    return unique


def _solid(ring: _Ring) -> Footprint:
    return Footprint(orient(ring.polygon, sign=1.0), ring.elevation, ring.sources)


def _assemble(rings: list[_Ring], stats: TopologyStats) -> list[Footprint]:
    """Regla par/impar por contención: profundidad impar = hueco de su contenedor inmediato."""
    if not rings:
        return []
    polygons = [r.polygon for r in rings]
    tree = shapely.STRtree(polygons)
    parent: list[int | None] = []
    depth: list[int] = []
    for i, polygon in enumerate(polygons):
        containers = [int(j) for j in tree.query(polygon, predicate="within") if int(j) != i]
        containers = [j for j in containers if polygons[j].area > polygon.area]
        depth.append(len(containers))
        parent.append(min(containers, key=lambda j: polygons[j].area) if containers else None)

    holes: dict[int, list[int]] = {}
    for i, d in enumerate(depth):
        owner = parent[i]
        if d % 2 == 1 and owner is not None:
            holes.setdefault(owner, []).append(i)
            stats.holes += 1

    footprints = []
    for i, ring in enumerate(rings):
        if depth[i] % 2 == 1:
            continue
        interiors = [polygons[h].exterior.coords for h in holes.get(i, [])]
        polygon = orient(Polygon(ring.polygon.exterior.coords, interiors), sign=1.0)
        footprints.append(Footprint(polygon, ring.elevation, ring.sources))
    return footprints
