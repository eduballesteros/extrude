"""Modelo intermedio + configuración → geometría 3D por capa (metros, Z-up, WCS sin recentrar).

El recentrado, el cambio a Y-up y la fusión de mallas se hacen al exportar (Fase 3).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import shapely
from numpy.typing import NDArray
from shapely.geometry import Polygon

from extrude.catalog import assign_ids
from extrude.config import EXTRUDED_KINDS, Config, HeightSource, Kind, LayerRule
from extrude.extrusion import MeshData, as_ground, drop_degenerate, extrude
from extrude.heights import (
    NO_ELEVATION_EPS_M,
    HeightOrigin,
    Neighbors,
    TextIndex,
    confidence,
    resolve_height,
)
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

# Zócalo a partir del cual se pide revisar la implantación (más de una planta de desnivel)
HIGH_FOUNDATION_M = 3.0
# Desnivel mínimo de las muestras para que un terreno aporte topografía (no un plano a cota 0)
MIN_TERRAIN_RELIEF_M = 1e-6


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
    sources: tuple[Provenance, ...] = ()  # entidades de origen (identidad persistente)
    # De dónde sale la cota de la base: terrain | terrain_extrapolated | source_elevation |
    # source_elevation_inferred | fixed
    base_origin: str | None = None
    # Qué dice la Z de la huella frente al terreno bajo ella: none | matches_terrain |
    # above_terrain | below_terrain (solo con terreno)
    source_z_role: str | None = None
    object_id: str | None = None  # catalog.assign_ids; independiente de capa exportada/tesela
    label: str | None = None  # etiqueta de usuario (E-001…), no es identidad


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
    # Toca el terreno en parte de la huella y queda en el aire en el resto (> tolerancia)
    partial_gap: int = 0
    unverified: int = 0  # no se pudo medir el contacto en la malla generada

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
            "partial_gap": self.partial_gap,
            "unverified": self.unverified,
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
    base_origins: Counter[str] = field(default_factory=Counter)
    # height_from: elevation cuya cubierta queda en la base o por debajo (se usó otra fuente)
    elevation_rejected: int = 0
    # Huellas con Z propia que no coincide con el terreno bajo ellas y que no se ha usado
    z_unexplained: int = 0


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
    # Terreno realmente usado para apoyar: estado, motivo, capas, muestras y rango de cotas
    terrain: dict[str, Any] = field(default_factory=dict)

    @property
    def triangle_count(self) -> int:
        return sum(g.stats.triangles for g in self.layers.values())


def build_scene(read: ReadResult, config: Config) -> BuildResult:
    scale = METERS_PER_UNIT[read.units]
    by_layer: dict[str, list[Feature]] = defaultdict(list)
    for feature in read.features:
        by_layer[feature.layer].append(feature)
    texts = TextIndex([f for f in read.features if isinstance(f, TextFeature)], scale)

    terrain, terrain_info = _terrain_surface(by_layer, config, scale)
    result = BuildResult(
        layers={}, unruled={}, ignored={}, without_geometry={}, warnings=[], terrain=terrain_info
    )
    result.warnings.extend(terrain_info.pop("warnings", []))
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

    assign_ids(result)
    if terrain is not None:
        _check_ground(result, terrain)
    floating = sorted(n for n, g in result.layers.items() if g.rule.base_from_terrain)
    if floating and terrain is None:
        why = terrain_info.get("reason") or "no hay ninguna capa kind: terrain"
        result.warnings.append(
            f"base_from_terrain sin terreno utilizable en: {', '.join(floating)} ({why}). "
            "Se usa base_elevation; añade una capa kind: terrain (curvas o puntos acotados)."
        )
    for name, geometry in result.layers.items():
        stats = geometry.stats
        if stats.elevation_rejected:
            result.warnings.append(
                f"Capa {name}: {stats.elevation_rejected} huellas con height_from: elevation "
                "tienen la cota de cubierta en la base o por debajo de ella; no dan altura y se "
                "usó la siguiente fuente (default_height es provisional). Revisa qué representa "
                "su cota Z."
            )
        if stats.z_unexplained:
            result.warnings.append(
                f"Capa {name}: {stats.z_unexplained} huellas tienen una cota Z propia que no "
                "coincide con el terreno bajo ellas. No se ha interpretado como base ni como "
                "cubierta: se apoyan en el terreno. Si es la cota de cubierta usa height_from: "
                "elevation; si es la base, base_from_elevation."
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
            sources=(mesh_feature.source,),
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
    # Apoyo en el terreno: pedido de forma explícita o decidido por base_auto si hay terreno
    ground = terrain if (rule.base_from_terrain or rule.base_auto) else None
    # base_auto sin terreno: la Z de la huella es la mejor referencia de base disponible
    z_as_base = rule.base_auto and ground is None and rule.height_from is not HeightSource.ELEVATION
    footprints, topo = build_footprints(
        polylines,
        hatches,
        scale=scale,
        tolerance=config.tolerance,
        detect_holes=rule.detect_holes,
        separate_elevations=rule.base_from_elevation
        or rule.height_from is HeightSource.ELEVATION
        or z_as_base,
    )
    stats.topology.add(topo)
    neighbors = Neighbors(footprints) if rule.height_from is not None else None
    for footprint in footprints:
        instance = _common_insert(footprint.sources)
        source_insert = instance
        has_z = abs(footprint.elevation) > NO_ELEVATION_EPS_M
        base = rule.base_elevation
        if rule.base_from_elevation:
            base += footprint.elevation
            base_origin = "source_elevation"
        elif z_as_base and has_z:
            base += footprint.elevation
            base_origin = "source_elevation_inferred"
        elif ground:
            covered = ground.covers(footprint.polygon)
            base_origin = "terrain" if covered else "terrain_extrapolated"
        else:
            base_origin = "fixed"
        low, high = ground.range_under(footprint.polygon) if ground else (0.0, 0.0)
        sloped = instance is None or high - low > FLAT_RELIEF_M
        on_foundation = (
            ground is not None
            and rule.kind is Kind.BUILDING
            and rule.terrain_support == "foundation"
            and high - low > 1e-6
        )
        # Cota absoluta real de la base: referencia de una cubierta absoluta (height_from:
        # elevation). Con zócalo la planta está en la cota máxima del terreno bajo la huella.
        reference = (high if on_foundation else low) + rule.base_elevation if ground else None
        resolved = resolve_height(footprint, rule, texts, neighbors, reference)
        stats.heights[resolved.origin] += 1
        stats.ambiguous_heights += int(resolved.ambiguous)
        stats.nearby_text_heights += int(resolved.nearby)
        if (
            rule.height_from is HeightSource.ELEVATION
            and resolved.origin is not HeightOrigin.ELEVATION
        ):
            stats.elevation_rejected += 1
        if resolved.height is None:
            stats.dropped_without_height += 1
            continue
        z_role = None
        if ground:
            if not has_z:
                z_role = "none"
            elif low - GROUND_TOLERANCE_M <= footprint.elevation <= high + GROUND_TOLERANCE_M:
                z_role = "matches_terrain"
            else:
                z_role = "above_terrain" if footprint.elevation > high else "below_terrain"
            # Solo cuando la decisión fue automática (base_auto) y es un edificio: con
            # base_from_terrain explícito el usuario ya eligió ignorar esa cota.
            if (
                rule.base_auto
                and rule.kind is Kind.BUILDING
                and z_role in ("above_terrain", "below_terrain")
                and resolved.origin is not HeightOrigin.ELEVATION
            ):
                stats.z_unexplained += 1
        stats.base_origins[base_origin] += 1
        foundation = on_foundation and resolved.height > 0
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
        if rule.kind is not Kind.BUILDING:
            raw = as_ground(raw)
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
                sources=footprint.sources,
                base_origin=base_origin,
                source_z_role=z_role,
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
    # Una topografía dibujada como malla (3DFACE, MESH) ya es la superficie visible: no se le
    # añade un plano base que la taparía ni que el apoyo desconoce.
    has_meshes = any(isinstance(f, MeshFeature) for f in features)
    if mesh is None and read.bbox is not None and not has_meshes:
        mesh = base_plane(read.bbox, scale, geometry.rule.base_elevation)
        geometry.stats.heights["base_plane"] += 1
    if mesh is not None:
        sources = tuple(f.source for f in features)
        _add(geometry, SceneObject(mesh, "TIN", _handles(features), sources=sources))


def _terrain_samples(
    features: Sequence[Feature], *, meshes: bool = False
) -> list[NDArray[np.float64]]:
    """Puntos acotados de una capa de terreno: curvas y puntos; con `meshes`, también los
    vértices de sus mallas 3D (la superficie de apoyo debe coincidir con la que se ve)."""
    samples = [f.points for f in features if isinstance(f, PolylineFeature)]
    samples += [f.position.reshape(1, 3) for f in features if isinstance(f, PointFeature)]
    if meshes:
        samples += [f.vertices for f in features if isinstance(f, MeshFeature)]
    return samples


def conflicting_duplicates(points: NDArray[np.float64], tolerance: float) -> int:
    """Posiciones XY con varias muestras cuya Z difiere más que la tolerancia: el terreno es
    ambiguo ahí (el TIN se queda con una sola)."""
    if len(points) == 0:
        return 0
    keys = np.round(points[:, :2] / tolerance).astype(np.int64)
    _, inverse = np.unique(keys, axis=0, return_inverse=True)
    inverse = inverse.ravel()
    low = np.full(inverse.max() + 1, np.inf)
    high = np.full(inverse.max() + 1, -np.inf)
    np.minimum.at(low, inverse, points[:, 2])
    np.maximum.at(high, inverse, points[:, 2])
    return int((high - low > max(tolerance, 1e-6)).sum())


def terrain_from_samples(
    samples: list[NDArray[np.float64]], scale: float, tolerance: float
) -> tuple[TerrainSurface | None, dict[str, Any]]:
    """Superficie de apoyo a partir de muestras acotadas y su diagnóstico. Un terreno solo es
    válido si se puede triangular y aporta cotas: no basta el nombre de la capa ni que alguna
    coordenada tenga Z."""
    info: dict[str, Any] = {"status": "absent", "reason": None, "points": 0, "warnings": []}
    if not samples:
        info["reason"] = "las capas de terreno no tienen curvas, puntos ni mallas acotadas"
        return None, info
    points = np.vstack(samples) * scale
    info["points"] = len(points)
    info["status"] = "invalid"
    if float(np.ptp(points[:, 2])) <= MIN_TERRAIN_RELIEF_M and abs(float(points[0, 2])) <= 1e-6:
        info["reason"] = "todas sus cotas son 0: el dibujo no aporta topografía"
        return None, info
    mesh = tin(points, tolerance)
    surface = TerrainSurface.from_points(mesh.vertices) if mesh is not None else None
    if surface is None:
        info["reason"] = (
            "sus puntos son insuficientes o están alineados: no se puede triangular una superficie"
        )
        return None, info
    low, high = surface.z_range
    duplicates = conflicting_duplicates(points, tolerance)
    info.update(
        status="valid",
        z_min_m=low,
        z_max_m=high,
        triangulated_points=len(mesh.vertices) if mesh is not None else 0,
        conflicting_duplicates=duplicates,
    )
    warnings: list[str] = []
    if duplicates:
        warnings.append(
            f"Terreno: {duplicates} posiciones tienen varias cotas distintas en el mismo punto "
            "XY. La superficie usa una sola de ellas: revisa esas curvas o puntos duplicados."
        )
    info["warnings"] = warnings
    return surface, info


def _terrain_surface(
    by_layer: dict[str, list[Feature]], config: Config, scale: float
) -> tuple[TerrainSurface | None, dict[str, Any]]:
    """TIN conjunto de todas las capas terrain: apoya las reglas con base_from_terrain o
    base_auto y sirve para comprobar que el resto de sólidos no flotan ni quedan enterrados."""
    layers = sorted(
        layer
        for layer in by_layer
        if (rule := config.rule_for(layer)) is not None and rule.kind is Kind.TERRAIN
    )
    if not layers:
        return None, {"status": "absent", "reason": None, "layers": [], "points": 0}
    samples = [s for layer in layers for s in _terrain_samples(by_layer[layer], meshes=True)]
    surface, info = terrain_from_samples(samples, scale, config.tolerance)
    info["layers"] = layers
    warnings = list(info.get("warnings") or [])
    if surface is None:
        warnings.append(
            f"El terreno ({', '.join(layers)}) no sirve para apoyar: {info['reason']}. "
            "Los objetos no se apoyan en él."
        )
    elif len(layers) > 1:
        warnings.append(
            f"Hay {len(layers)} capas de terreno ({', '.join(layers)}): el apoyo usa la "
            "superficie conjunta de todas, que entre capas puede no coincidir con la "
            "triangulación dibujada de cada una."
        )
    info["warnings"] = warnings
    return surface, info


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
                # …pero el contacto se MIDE en la malla: el punto más bajo de cada XY frente al
                # terreno (no se da por bueno por haberse generado siguiendo el TIN). Los XY se
                # agrupan a 0,01 mm: tapa y paredes calculan el mismo punto por caminos distintos.
                xy, inverse = np.unique(
                    np.round(obj.mesh.vertices[:, :2], 5), axis=0, return_inverse=True
                )
                lower = np.full(len(xy), np.inf)
                np.minimum.at(lower, inverse.ravel(), obj.mesh.vertices[:, 2])
                if (obj.height or 0.0) > 0 and obj.footprint is not None:
                    # Con espesor, el interior solo tiene la cara superior: el contacto se mide
                    # en el contorno, donde están los pies de las paredes.
                    edge = shapely.distance(obj.footprint.boundary, shapely.points(xy)) < 1e-6
                    if edge.any():
                        xy, lower = xy[edge], lower[edge]
                clearance = lower - terrain.height(xy)
                contact_ranges[i] = (float(clearance.min()), float(clearance.max()))
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
        # Contacto parcial: toca (o entra) en la parte alta del terreno y queda en el aire
        # sobre la parte baja. No es flotación completa, pero tampoco apoyo completo.
        # Las categorías son excluyentes: si además corta la ladera o queda parcialmente
        # enterrado, cuenta como eso (el problema mayor).
        clear = ~floating & ~buried & ~intersecting & ~partially_buried
        partial_gap = clear & (base > ground_low + GROUND_TOLERANCE_M)
        for i, (_, gap_high) in contact_ranges.items():
            partial_gap[i] = bool(clear[i]) and gap_high > GROUND_TOLERANCE_M
        unverified = np.array(
            [
                o.floor_elevation is not None and i not in contact_ranges
                for i, o in enumerate(solids)
            ]
        )
        check = GroundCheck(
            checked=len(solids),
            floating=int(floating.sum()),
            buried=int(buried.sum()),
            intersecting=int(intersecting.sum()),
            partially_buried=int(partially_buried.sum()),
            partial_gap=int(partial_gap.sum()),
            unverified=int(unverified.sum()),
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
        nominal = ~floating & ~buried & ~intersecting & ~partially_buried & ~partial_gap
        foundations = np.array([o.foundation_height > 0 for o in solids])
        for category, mask in (
            ("partial_gap", partial_gap),
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
                        "object_id": obj.object_id,
                        "label": obj.label,
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
                        "height_confidence": confidence(obj.height_origin),
                        "source_z_role": obj.source_z_role,
                        "floor_m": obj.floor_elevation,
                        "foundation_height_m": obj.foundation_height,
                        "contact_clearance_m": list(contact_ranges[i])
                        if i in contact_ranges
                        else None,
                        "base_mode": (
                            "terrain_foundation"
                            if obj.floor_elevation is not None
                            else "terrain_min"
                            if (obj.base_origin or "").startswith("terrain")
                            else obj.base_origin or "fixed"
                        ),
                        "base_origin": obj.base_origin,
                        "terrain_min_m": float(ground_low[i]),
                        "terrain_max_m": float(ground_high[i]),
                        "terrain_covered": coverage[i],
                    }
                )
        if rule.base_from_elevation and check.misplaced:
            elevation_suspects.append((name, check))
        elif not (rule.base_from_terrain or rule.base_auto) and check.misplaced:
            # Cualquier sólido que flote o quede enterrado se avisa (mismo criterio en el visor)
            suspects.append((name, check))
        if check.partial_gap:
            result.warnings.append(
                f"Capa {name}: {check.partial_gap} sólidos solo tocan el terreno en parte de su "
                f"huella y quedan en el aire en el resto (más de {GROUND_TOLERANCE_M:g} m)."
            )
        if check.unverified:
            result.warnings.append(
                f"Capa {name}: en {check.unverified} sólidos no se pudo medir el contacto con el "
                "terreno en la malla generada (contacto no verificado)."
            )
    low, high = terrain.z_range
    if suspects:
        names = ", ".join(n for n, _ in suspects)
        counts = "; ".join(_misplaced(n, c) for n, c in suspects)
        result.warnings.append(
            f"Las capas {names} no usan base_from_terrain y el terreno va de {low:.1f} a "
            f"{high:.1f} m: sus sólidos flotan o quedan enterrados ({counts}). Usa «Revisar "
            "apoyo» en el visor, o base_auto: true en su regla del YAML (base_elevation si la "
            "base es constante)."
        )
    for name, check in elevation_suspects:
        result.warnings.append(
            f"La capa {name} usa base_from_elevation pero {_counts(check)} frente al "
            f"terreno ({low:.1f} a {high:.1f} m): probablemente plantas sin cota (Z = 0). Si la "
            "cota de las plantas no es fiable, usa «Revisar apoyo» en el visor (base_auto: true)."
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
