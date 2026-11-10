"""Modelo intermedio + configuración → geometría 3D por capa (metros, Z-up, WCS sin recentrar).

El recentrado, el cambio a Y-up y la fusión de mallas se hacen al exportar (Fase 3).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from extrude.config import EXTRUDED_KINDS, Config, Kind, LayerRule
from extrude.extrusion import MeshData, drop_degenerate, extrude
from extrude.heights import HeightOrigin, TextIndex, resolve_height
from extrude.meshes3d import base_plane, mesh_from_feature, tin
from extrude.model import (
    METERS_PER_UNIT,
    Feature,
    HatchFeature,
    MeshFeature,
    PointFeature,
    PolylineFeature,
    ReadResult,
    TextFeature,
)
from extrude.topology import TopologyStats, build_footprints


@dataclass(frozen=True, slots=True)
class SceneObject:
    """Un edificio, vial, malla… con sus metadatos (irán a `extras` del glTF)."""

    mesh: MeshData
    source_type: str
    handles: tuple[str, ...]
    height: float | None = None
    height_origin: HeightOrigin | None = None


@dataclass
class LayerStats:
    features: Counter[str] = field(default_factory=Counter)
    topology: TopologyStats = field(default_factory=TopologyStats)
    heights: Counter[str] = field(default_factory=Counter)
    ambiguous_heights: int = 0
    dropped_without_height: int = 0
    degenerate_triangles: int = 0
    triangles: int = 0


@dataclass
class LayerGeometry:
    layer: str
    rule: LayerRule
    objects: list[SceneObject] = field(default_factory=list)
    lines: list[NDArray[np.float64]] = field(default_factory=list)
    stats: LayerStats = field(default_factory=LayerStats)


@dataclass
class BuildResult:
    layers: dict[str, LayerGeometry]
    unruled: dict[str, Counter[str]]  # capa → tipo DXF → nº (sin regla en el YAML)
    ignored: dict[str, Counter[str]]  # capa → tipo DXF → nº (kind: ignore)
    without_geometry: dict[str, Counter[str]]  # con regla pero sin nada que convertir (textos…)
    warnings: list[str]

    @property
    def triangle_count(self) -> int:
        return sum(g.stats.triangles for g in self.layers.values())


def build_scene(read: ReadResult, config: Config) -> BuildResult:
    scale = METERS_PER_UNIT[read.units]
    by_layer: dict[str, list[Feature]] = defaultdict(list)
    for feature in read.features:
        by_layer[feature.layer].append(feature)
    texts = TextIndex([f for f in read.features if isinstance(f, TextFeature)], scale)

    result = BuildResult(layers={}, unruled={}, ignored={}, without_geometry={}, warnings=[])
    for layer, features in sorted(by_layer.items()):
        counts = Counter(f.source.dxftype for f in features)
        rule = config.rule_for(layer)
        if rule is None:
            result.unruled[layer] = counts
            continue
        if rule.kind is Kind.IGNORE:
            result.ignored[layer] = counts
            continue
        geometry = LayerGeometry(layer, rule)
        geometry.stats.features = counts
        _build_layer(geometry, features, read, config, texts, scale)
        if geometry.objects or geometry.lines or geometry.stats.dropped_without_height:
            result.layers[layer] = geometry
        else:
            result.without_geometry[layer] = counts

    if result.unruled:
        names = ", ".join(sorted(result.unruled))
        result.warnings.append(
            f"Capas sin regla en la configuración (no se convierten): {names}. "
            'Añade una regla para cada una o una regla final match: "*".'
        )
    return result


def _build_layer(
    geometry: LayerGeometry,
    features: list[Feature],
    read: ReadResult,
    config: Config,
    texts: TextIndex,
    scale: float,
) -> None:
    rule, stats = geometry.rule, geometry.stats
    polylines = [f for f in features if isinstance(f, PolylineFeature)]
    hatches = [f for f in features if isinstance(f, HatchFeature)]

    for mesh_feature in (f for f in features if isinstance(f, MeshFeature)):
        mesh, degenerate = mesh_from_feature(mesh_feature, scale)
        stats.degenerate_triangles += degenerate
        _add(geometry, SceneObject(mesh, mesh_feature.source.dxftype, _handles([mesh_feature])))

    if rule.kind in EXTRUDED_KINDS:
        _extrude_layer(geometry, polylines, hatches, config, texts, scale)
    elif rule.kind is Kind.LINE:
        geometry.lines.extend(f.points * scale for f in polylines)
        geometry.lines.extend(loop * scale for h in hatches for loop in h.loops)
    elif rule.kind is Kind.TERRAIN:
        _terrain_layer(geometry, features, read, config, scale)


def _extrude_layer(
    geometry: LayerGeometry,
    polylines: list[PolylineFeature],
    hatches: list[HatchFeature],
    config: Config,
    texts: TextIndex,
    scale: float,
) -> None:
    rule, stats = geometry.rule, geometry.stats
    footprints, topo = build_footprints(
        polylines,
        hatches,
        scale=scale,
        tolerance=config.tolerance,
        detect_holes=rule.detect_holes,
    )
    stats.topology.add(topo)
    for footprint in footprints:
        resolved = resolve_height(footprint, rule, texts)
        stats.heights[resolved.origin] += 1
        stats.ambiguous_heights += int(resolved.ambiguous)
        if resolved.height is None:
            stats.dropped_without_height += 1
            continue
        raw = extrude(
            footprint.polygon, rule.base_elevation, resolved.height, bottom=rule.bottom_cap
        )
        mesh, degenerate = drop_degenerate(raw)
        stats.degenerate_triangles += degenerate
        source_type = "+".join(sorted({s.dxftype for s in footprint.sources}))
        handles = tuple(s.handle for s in footprint.sources if s.handle)
        _add(
            geometry,
            SceneObject(mesh, source_type, handles, resolved.height, resolved.origin),
        )


def _terrain_layer(
    geometry: LayerGeometry,
    features: list[Feature],
    read: ReadResult,
    config: Config,
    scale: float,
) -> None:
    samples = [f.points for f in features if isinstance(f, PolylineFeature)]
    samples += [f.position.reshape(1, 3) for f in features if isinstance(f, PointFeature)]
    mesh = tin(np.vstack(samples) * scale, config.tolerance) if samples else None
    if mesh is None and read.bbox is not None:
        mesh = base_plane(read.bbox, scale, geometry.rule.base_elevation)
        geometry.stats.heights["base_plane"] += 1
    if mesh is not None:
        _add(geometry, SceneObject(mesh, "TIN", _handles(features)))


def _add(geometry: LayerGeometry, obj: SceneObject) -> None:
    geometry.objects.append(obj)
    geometry.stats.triangles += obj.mesh.triangle_count


def _handles(features: Sequence[Feature]) -> tuple[str, ...]:
    return tuple(f.source.handle for f in features if f.source.handle)
