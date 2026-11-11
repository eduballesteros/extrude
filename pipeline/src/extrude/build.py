"""Modelo intermedio + configuración → geometría 3D por capa (metros, Z-up, WCS sin recentrar).

El recentrado, el cambio a Y-up y la fusión de mallas se hacen al exportar (Fase 3).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray
from shapely.geometry import Polygon

from extrude.config import EXTRUDED_KINDS, Config, HeightSource, Kind, LayerRule
from extrude.extrusion import MeshData, drop_degenerate, extrude
from extrude.heights import HeightOrigin, Neighbors, TextIndex, resolve_height
from extrude.meshes3d import base_plane, mesh_from_feature, tin
from extrude.model import (
    METERS_PER_UNIT,
    Feature,
    HatchFeature,
    InsertRef,
    MeshFeature,
    PointFeature,
    PolylineFeature,
    Provenance,
    ReadResult,
    TextFeature,
)
from extrude.terrain import FLAT_RELIEF_M, GROUND_TOLERANCE_M, TerrainSurface
from extrude.topology import TopologyStats, build_footprints

# Fracción de sólidos mal apoyados de una capa a partir de la cual se avisa (≥ 1 siempre)
MISPLACED_FRACTION = 0.1


@dataclass(frozen=True, slots=True)
class SceneObject:
    """Un edificio, vial, malla… con sus metadatos (irán a `extras` del glTF)."""

    mesh: MeshData
    source_type: str
    handles: tuple[str, ...]
    height: float | None = None
    height_origin: HeightOrigin | None = None
    instance: InsertRef | None = None  # si todo el objeto sale de un único INSERT
    hard_edges: bool = False  # malla 3D de origen: normales por cara (no suavizar)
    footprint: Polygon | None = None  # huella real, con concavidades y patios, para GroundCheck
    draped: bool = False  # la base sigue el TIN con un desplazamiento constante
    source_elevation: float | None = None  # cota original de la huella, en metros
    floor_elevation: float | None = None  # planta horizontal; excluye el zócalo
    foundation_height: float = 0.0
    source_insert: InsertRef | None = None  # procedencia aun si el apoyo impide instanciar


@dataclass(frozen=True, slots=True)
class LineObject:
    points: NDArray[np.float64]  # (N, 3) metros
    closed: bool


@dataclass(frozen=True)
class GroundCheck:
    """Sólidos extruidos de una capa frente al terreno (TIN) bajo su contorno."""

    checked: int
    floating: int  # base más de GROUND_TOLERANCE_M por encima de todo el terreno
    buried: int  # cubierta por debajo de todo el terreno
    intersecting: int = 0  # terreno sobre la base, sin superar la cubierta
    partially_buried: int = 0  # terreno supera la cubierta solo en parte de la huella

    @property
    def misplaced(self) -> int:
        return self.floating + self.buried

    def as_dict(self) -> dict[str, int]:
        return {
            "checked": self.checked,
            "floating": self.floating,
            "buried": self.buried,
            "intersecting": self.intersecting,
            "partially_buried": self.partially_buried,
        }


@dataclass
class LayerStats:
    features: Counter[str] = field(default_factory=Counter)
    topology: TopologyStats = field(default_factory=TopologyStats)
    heights: Counter[str] = field(default_factory=Counter)
    ambiguous_heights: int = 0
    nearby_text_heights: int = 0  # de un texto justo fuera del contorno (rescate)
    dropped_without_height: int = 0
    degenerate_triangles: int = 0
    triangles: int = 0
    ground: GroundCheck | None = None  # solo si hay terreno
    ground_details: list[dict[str, object]] = field(default_factory=list)
    terrain_coverage: dict[str, int] | None = None


@dataclass
class LayerGeometry:
    layer: str
    rule: LayerRule
    objects: list[SceneObject] = field(default_factory=list)
    lines: list[LineObject] = field(default_factory=list)
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

    terrain = _terrain_surface(by_layer, config, scale)
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
        _build_layer(geometry, features, read, config, texts, scale, terrain)
        if geometry.objects or geometry.lines or geometry.stats.dropped_without_height:
            result.layers[layer] = geometry
        else:
            result.without_geometry[layer] = counts

    if terrain is not None:
        _check_ground(result, terrain)
    floating = sorted(n for n, g in result.layers.items() if g.rule.base_from_terrain)
    if floating and terrain is None:
        result.warnings.append(
            f"base_from_terrain sin terreno con cotas en: {', '.join(floating)}. "
            "Se usa base_elevation; añade una capa kind: terrain (curvas o puntos acotados)."
        )
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
    terrain: TerrainSurface | None = None,
) -> None:
    rule, stats = geometry.rule, geometry.stats
    polylines = [f for f in features if isinstance(f, PolylineFeature)]
    hatches = [f for f in features if isinstance(f, HatchFeature)]

    for mesh_feature in (f for f in features if isinstance(f, MeshFeature)):
        mesh, degenerate = mesh_from_feature(mesh_feature, scale)
        stats.degenerate_triangles += degenerate
        obj = SceneObject(
            mesh,
            mesh_feature.source.dxftype,
            _handles([mesh_feature]),
            instance=mesh_feature.source.insert,
            hard_edges=True,
        )
        _add(geometry, obj)

    if rule.kind in EXTRUDED_KINDS:
        _extrude_layer(geometry, polylines, hatches, config, texts, scale, terrain)
    elif rule.kind is Kind.LINE:
        geometry.lines.extend(LineObject(f.points * scale, f.closed) for f in polylines)
        geometry.lines.extend(LineObject(loop * scale, True) for h in hatches for loop in h.loops)
    elif rule.kind is Kind.TERRAIN:
        _terrain_layer(geometry, features, read, config, scale)


def _extrude_layer(
    geometry: LayerGeometry,
    polylines: list[PolylineFeature],
    hatches: list[HatchFeature],
    config: Config,
    texts: TextIndex,
    scale: float,
    terrain: TerrainSurface | None = None,
) -> None:
    rule, stats = geometry.rule, geometry.stats
    ground = terrain if rule.base_from_terrain else None
    footprints, topo = build_footprints(
        polylines,
        hatches,
        scale=scale,
        tolerance=config.tolerance,
        detect_holes=rule.detect_holes,
        separate_elevations=rule.base_from_elevation or rule.height_from is HeightSource.ELEVATION,
    )
    stats.topology.add(topo)
    neighbors = Neighbors(footprints) if rule.height_from is not None else None
    for footprint in footprints:
        resolved = resolve_height(footprint, rule, texts, neighbors)
        stats.heights[resolved.origin] += 1
        stats.ambiguous_heights += int(resolved.ambiguous)
        stats.nearby_text_heights += int(resolved.nearby)
        if resolved.height is None:
            stats.dropped_without_height += 1
            continue
        instance = _common_insert(footprint.sources)
        source_insert = instance
        base = rule.base_elevation + (footprint.elevation if rule.base_from_elevation else 0.0)
        low, high = ground.range_under(footprint.polygon) if ground else (0.0, 0.0)
        sloped = instance is None or high - low > FLAT_RELIEF_M
        foundation = (
            ground is not None
            and rule.kind is Kind.BUILDING
            and resolved.height > 0
            and rule.terrain_support == "foundation"
            and high - low > 1e-6
        )
        # Una altura nula representa una superficie, también en capas building: no hay
        # volumen ni planta elevada que requiera un zócalo. Sigue el TIN incluso en bloques.
        draped = ground is not None and (
            resolved.height == 0 or (rule.kind is not Kind.BUILDING and sloped)
        )
        floor = None
        if ground and foundation:
            floor = high + rule.base_elevation
            raw = ground.building(
                footprint.polygon,
                floor,
                resolved.height,
                rule.base_elevation,
                bottom=rule.bottom_cap,
            )
            instance = None  # cada zócalo sigue su terreno; no comparte prototipo
        elif ground and draped:
            raw = ground.drape(footprint.polygon, rule.base_elevation, resolved.height)
            instance = None  # cada copia sigue su trozo de terreno: ya no es la misma forma
        else:
            if ground:
                base += low
                instance = _at_elevation(instance, base / scale)
            raw = extrude(footprint.polygon, base, resolved.height, bottom=rule.bottom_cap)
        mesh, degenerate = drop_degenerate(raw)
        stats.degenerate_triangles += degenerate
        source_type = "+".join(sorted({s.dxftype for s in footprint.sources}))
        handles = tuple(s.handle for s in footprint.sources if s.handle)
        _add(
            geometry,
            SceneObject(
                mesh,
                source_type,
                handles,
                resolved.height,
                resolved.origin,
                instance=instance,
                footprint=footprint.polygon,
                draped=draped,
                source_elevation=footprint.elevation,
                floor_elevation=floor,
                foundation_height=high - low if foundation else 0.0,
                source_insert=source_insert,
            ),
        )


def _terrain_layer(
    geometry: LayerGeometry,
    features: list[Feature],
    read: ReadResult,
    config: Config,
    scale: float,
) -> None:
    samples = _terrain_samples(features)
    mesh = tin(np.vstack(samples) * scale, config.tolerance) if samples else None
    if mesh is None and read.bbox is not None:
        mesh = base_plane(read.bbox, scale, geometry.rule.base_elevation)
        geometry.stats.heights["base_plane"] += 1
    if mesh is not None:
        _add(geometry, SceneObject(mesh, "TIN", _handles(features)))


def _terrain_samples(features: Sequence[Feature]) -> list[NDArray[np.float64]]:
    samples = [f.points for f in features if isinstance(f, PolylineFeature)]
    samples += [f.position.reshape(1, 3) for f in features if isinstance(f, PointFeature)]
    return samples


def _terrain_surface(
    by_layer: dict[str, list[Feature]], config: Config, scale: float
) -> TerrainSurface | None:
    """TIN conjunto de todas las capas terrain: apoya las reglas con base_from_terrain y sirve
    para comprobar que el resto de sólidos no flotan ni quedan enterrados."""
    samples = [
        s
        for layer, features in by_layer.items()
        if (rule := config.rule_for(layer)) is not None and rule.kind is Kind.TERRAIN
        for s in _terrain_samples(features)
    ]
    mesh = tin(np.vstack(samples) * scale, config.tolerance) if samples else None
    return TerrainSurface.from_points(mesh.vertices) if mesh is not None else None


def _check_ground(result: BuildResult, terrain: TerrainSurface) -> None:
    """Compara cada sólido extruido con el terreno bajo su contorno y avisa de:
    - capas sin base_from_terrain ni base_from_elevation que flotan o se hunden (≥ 10 %);
    - capas con base_from_elevation con algún sólido así (plantas sin cota, Z = 0, o mal acotadas).
    """
    suspects: list[tuple[str, GroundCheck]] = []
    elevation_suspects: list[tuple[str, GroundCheck]] = []
    for name, geometry in result.layers.items():
        rule = geometry.rule
        solids = [o for o in geometry.objects if o.height is not None and len(o.mesh.vertices)]
        if rule.kind not in EXTRUDED_KINDS or not solids:
            continue
        # Terreno en todos los vértices de cada sólido (una sola interpolación vectorizada)
        sizes = np.array([len(o.mesh.vertices) for o in solids])
        vertices = np.vstack([o.mesh.vertices for o in solids])
        starts = np.concatenate([[0], np.cumsum(sizes)[:-1]])
        ground = terrain.height(vertices[:, :2])
        ground_low = np.minimum.reduceat(ground, starts)
        ground_high = np.maximum.reduceat(ground, starts)
        base = np.minimum.reduceat(vertices[:, 2], starts)
        top = np.maximum.reduceat(vertices[:, 2], starts)
        contact_ranges: dict[int, tuple[float, float]] = {}
        for i, obj in enumerate(solids):
            if obj.draped:
                # Superficies paralelas al TIN: el desnivel de la ladera no es penetración.
                ground_low[i] = ground_high[i] = 0.0
                base[i] = rule.base_elevation
                top[i] = base[i] + (obj.height or 0.0)
            elif obj.footprint is not None:
                ground_low[i], ground_high[i] = terrain.range_under(obj.footprint)
            if obj.floor_elevation is not None:
                # Comprobar la geometría creada, no darla por válida por su modo de apoyo.
                # El mínimo Z en cada XY es el pie del zócalo (o la tapa inferior).
                xy, inverse = np.unique(obj.mesh.vertices[:, :2], axis=0, return_inverse=True)
                lower = np.full(len(xy), np.inf)
                np.minimum.at(lower, inverse, obj.mesh.vertices[:, 2])
                feet = lower < top[i] - 1e-8  # excluir vértices interiores solo de cubierta
                if not feet.any():
                    result.warnings.append(
                        f"Capa {name}, objeto {i + 1}: no se pudo comprobar el contacto del "
                        "zócalo porque la malla no tiene puntos inferiores a la cubierta. "
                        "Se mantienen las comprobaciones generales de cotas; revisa su geometría."
                    )
                    continue
                clearance = lower[feet] - terrain.height(xy[feet])
                contact_ranges[i] = (float(clearance.min()), float(clearance.max()))
        floating = base > ground_high + GROUND_TOLERANCE_M
        penetrating = base < ground_high - GROUND_TOLERANCE_M
        for i, (gap_low, _) in contact_ranges.items():
            floating[i] = gap_low > GROUND_TOLERANCE_M
            penetrating[i] = gap_low < -GROUND_TOLERANCE_M
        buried = top < ground_low - GROUND_TOLERANCE_M
        partially_buried = ~buried & (top < ground_high - GROUND_TOLERANCE_M)
        intersecting = ~buried & ~partially_buried & penetrating
        check = GroundCheck(
            checked=len(solids),
            floating=int(floating.sum()),
            buried=int(buried.sum()),
            intersecting=int(intersecting.sum()),
            partially_buried=int(partially_buried.sum()),
        )
        geometry.stats.ground = check
        coverage = [
            terrain.covers(o.footprint) if o.footprint is not None else None for o in solids
        ]
        extrapolated = sum(c is False for c in coverage)
        geometry.stats.terrain_coverage = {
            "checked": len(solids),
            "covered": sum(c is True for c in coverage),
            "extrapolated": extrapolated,
            "unknown": sum(c is None for c in coverage),
        }
        if extrapolated:
            result.warnings.append(
                f"Capa {name}: {extrapolated} de {len(solids)} huellas salen del TIN. "
                "El apoyo exterior se estima con el punto de terreno más cercano; "
                "no hay cobertura de terreno bajo toda la huella. Revisa o amplía la topografía."
            )
        geometry.stats.ground_details = []
        nominal = ~floating & ~buried & ~intersecting & ~partially_buried
        foundations = np.array([o.foundation_height > 0 for o in solids])
        unverified = np.array(
            [
                o.floor_elevation is not None and i not in contact_ranges
                for i, o in enumerate(solids)
            ]
        )
        for category, mask in (
            ("floating", floating),
            ("buried", buried),
            ("intersecting", intersecting),
            ("partially_buried", partially_buried),
            (
                "foundation",
                foundations & ~unverified & nominal,
            ),
            ("supported", nominal & ~foundations & ~unverified),
            ("unverified_contact", nominal & unverified),
        ):
            for index in np.flatnonzero(mask):
                i = int(index)
                obj = solids[i]
                provenance = obj.source_insert or obj.instance
                center = obj.footprint.representative_point() if obj.footprint is not None else None
                geometry.stats.ground_details.append(
                    {
                        "category": category,
                        "handles": list(obj.handles),
                        "insert_handle": (
                            provenance.handle.partition("#")[0] if provenance else None
                        ),
                        "instance_id": provenance.handle if provenance else None,
                        "block": provenance.block if provenance else None,
                        "xy_m": [center.x, center.y] if center is not None else None,
                        "source_elevation_m": obj.source_elevation,
                        "base_m": float(base[i]),
                        "top_m": float(top[i]),
                        "height_m": obj.height,
                        "height_origin": str(obj.height_origin),
                        "floor_m": obj.floor_elevation,
                        "foundation_height_m": obj.foundation_height,
                        "contact_clearance_m": list(contact_ranges[i])
                        if i in contact_ranges
                        else None,
                        "base_mode": (
                            "terrain_foundation"
                            if obj.floor_elevation is not None
                            else "terrain_min"
                            if rule.base_from_terrain
                            else "source_elevation"
                            if rule.base_from_elevation
                            else "fixed"
                        ),
                        "terrain_min_m": float(ground_low[i]),
                        "terrain_max_m": float(ground_high[i]),
                        "terrain_covered": coverage[i],
                    }
                )
        if rule.base_from_elevation and check.misplaced:
            elevation_suspects.append((name, check))
        elif not rule.base_from_terrain and check.misplaced >= max(
            1, MISPLACED_FRACTION * check.checked
        ):
            suspects.append((name, check))
    low, high = terrain.z_range
    if suspects:
        names = ", ".join(n for n, _ in suspects)
        counts = "; ".join(_misplaced(n, c) for n, c in suspects)
        result.warnings.append(
            f"Las capas {names} no usan base_from_terrain y el terreno va de {low:.1f} a "
            f"{high:.1f} m: sus sólidos flotan o quedan enterrados ({counts}). Añade "
            "base_from_terrain: true en su regla del YAML (o base_elevation si la base es "
            "constante)."
        )
    for name, check in elevation_suspects:
        result.warnings.append(
            f"La capa {name} usa base_from_elevation pero {_counts(check)} frente al "
            f"terreno ({low:.1f} a {high:.1f} m): probablemente plantas sin cota (Z = 0). Si la "
            "cota de las plantas no es fiable, usa base_from_terrain: true."
        )


def _counts(check: GroundCheck) -> str:
    return f"{check.floating} flotan, {check.buried} enterrados de {check.checked}"


def _misplaced(name: str, check: GroundCheck) -> str:
    return f"{name}: {_counts(check)}"


def _at_elevation(instance: InsertRef | None, z: float) -> InsertRef | None:
    """Mueve el INSERT a la cota z (unidades del dibujo) para que la copia siga siendo
    instanciable: la malla se ha apoyado en el terreno, no en la Z original del bloque."""
    if instance is None:
        return None
    matrix = list(instance.matrix)
    matrix[14] = z
    return InsertRef(instance.handle, instance.block, tuple(matrix))


def _add(geometry: LayerGeometry, obj: SceneObject) -> None:
    geometry.objects.append(obj)
    geometry.stats.triangles += obj.mesh.triangle_count


def _common_insert(sources: Sequence[Provenance]) -> InsertRef | None:
    inserts = {s.insert for s in sources}
    return inserts.pop() if len(inserts) == 1 else None


def _handles(features: Sequence[Feature]) -> tuple[str, ...]:
    return tuple(f.source.handle for f in features if f.source.handle)
