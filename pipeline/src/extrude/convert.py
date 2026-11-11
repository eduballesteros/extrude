"""Orquestación de `extrude convert`: DWG/DXF → GLB (o teselas) + informe."""

from __future__ import annotations

import json
import math
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from extrude.build import BuildResult, LineObject, SceneObject, build_scene
from extrude.catalog import (
    CATALOG_NAME,
    build_catalog,
    export_refs,
    geometry_revision,
    write_catalog,
)
from extrude.config import Config
from extrude.converters import ensure_dxf
from extrude.dxf_reader import read_dxf
from extrude.errors import ExtrudeError
from extrude.extrusion import box_corners
from extrude.geodesy import to_wgs84
from extrude.gltf_export import ExportLayer, GlbStats, SceneInfo, build_glb
from extrude.instancing import InstanceGroup, plan_instances, transform
from extrude.model import METERS_PER_UNIT, LengthUnit, ReadResult
from extrude.node_tools import Compression, compress
from extrude.report import write_reports

MODEL_NAME = "model.glb"
TILES_DIR = "tiles"
MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ConvertOptions:
    config: Config
    out_dir: Path
    tile_size: float | None = None
    compression: Compression | None = None
    units_override: LengthUnit | None = None
    # Se llama al empezar cada fase (dwg_to_dxf, read, build…): progreso para extrude serve
    on_phase: Callable[[str], None] | None = field(default=None, compare=False, repr=False)


@dataclass
class OutputFile:
    path: Path
    stats: GlbStats
    bbox: dict[str, list[float]] | None = None
    tile: tuple[int, int] | None = None


@dataclass
class ConvertResult:
    read: ReadResult
    build: BuildResult
    layers: list[ExportLayer]
    origin: tuple[float, float, float]
    outputs: list[OutputFile]
    timings: dict[str, float] = field(default_factory=dict)
    report: dict[str, Any] = field(default_factory=dict)
    geometry_revision: str | None = None
    catalog: dict[str, Any] = field(default_factory=dict)


class _Timer:
    def __init__(self, on_phase: Callable[[str], None] | None = None) -> None:
        self.timings: dict[str, float] = {}
        self.on_phase = on_phase

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        if self.on_phase is not None:
            self.on_phase(name)
        start = time.perf_counter()
        yield
        self.timings[name] = round(time.perf_counter() - start, 3)


def convert(source: Path, options: ConvertOptions) -> ConvertResult:
    if options.tile_size is not None and (
        not math.isfinite(options.tile_size) or options.tile_size <= 0
    ):
        raise ExtrudeError("El tamaño de tesela debe ser finito y > 0.", "Ejemplo: --tiles 500.")
    timer = _Timer(options.on_phase)
    config = options.config
    options.out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="extrude-") as workdir:
        with timer.phase("dwg_to_dxf"):
            dxf = ensure_dxf(source, Path(workdir))
        with timer.phase("read"):
            units = options.units_override or config.units_override
            read = read_dxf(dxf, units_override=units, flatten_tolerance_m=config.tolerance)
    with timer.phase("build"):
        build = build_scene(read, config)
    scale = METERS_PER_UNIT[read.units]
    with timer.phase("instancing"):
        layers = _export_layers(build, config, scale)
    origin = _origin(layers) if config.recenter else (0.0, 0.0, 0.0)
    center = _center_xy(layers)
    geographic = to_wgs84(config.crs, *center) if center else None
    if config.crs and center and geographic is None:
        build.warnings.append(
            f"No se pudo geolocalizar el centro con {config.crs}: "
            "revisa el CRS y las coordenadas. Se exporta el modelo sin ubicación solar."
        )
    revision = geometry_revision(build, config.crs)
    info = SceneInfo(
        origin=origin,
        source_units=read.units.value,
        crs=config.crs,
        center_lat=geographic[0] if geographic else None,
        center_lon=geographic[1] if geographic else None,
        geometry_revision=revision,
    )

    with timer.phase("export"):
        if options.tile_size:
            outputs = _write_tiles(layers, info, options.out_dir, options.tile_size)
        else:
            outputs = [_write(layers, info, options.out_dir / MODEL_NAME)]
        # Las referencias a nodos solo valen para un GLB único; con teselas el ID es la única clave.
        tiled = any(o.tile for o in outputs)
        catalog = build_catalog(
            build,
            revision=revision,
            source_units=read.units.value,
            crs=config.crs,
            origin=origin,
            export_refs=None if tiled else export_refs(layers),
        )
        write_catalog(catalog, options.out_dir / CATALOG_NAME)
    if options.compression is not None:
        with timer.phase("compress"):
            for output in outputs:
                _compress_in_place(output, options.compression)
    if options.tile_size and any(o.tile for o in outputs):
        _write_manifest(outputs, info, options.out_dir, options.tile_size)

    result = ConvertResult(read, build, layers, origin, outputs, timer.timings)
    result.geometry_revision = revision
    result.catalog = catalog
    result.report = write_reports(result, source, options)
    return result


def _export_layers(build: BuildResult, config: Config, scale: float) -> list[ExportLayer]:
    layers = []
    for name, geometry in build.layers.items():
        groups, loose = plan_instances(
            geometry.objects,
            scale=scale,
            min_count=config.instancing_min,
            tolerance=config.tolerance,
        )
        layers.append(
            ExportLayer(
                name=name,
                rule=geometry.rule,
                objects=loose,
                groups=groups,
                lines=geometry.lines,
                source_entities=dict(geometry.stats.features),
                ground_check=geometry.stats.ground.as_dict() if geometry.stats.ground else None,
            )
        )
    return layers


def _bounds(layers: list[ExportLayer]) -> tuple[NDArray[np.float64], NDArray[np.float64]] | None:
    points: list[NDArray[np.float64]] = []
    for layer in layers:
        points.extend(o.mesh.vertices for o in layer.objects if len(o.mesh.vertices))
        points.extend(line.points for line in layer.lines)
        for group in layer.groups:
            corners = box_corners(group.prototype.vertices)
            for matrix in group.matrices:
                points.append(transform(corners, matrix))
    if not points:
        return None
    stacked = np.vstack(points)
    return stacked.min(axis=0), stacked.max(axis=0)


def _center_xy(layers: list[ExportLayer]) -> tuple[float, float] | None:
    """Centro XY del bbox en metros (float64), en coordenadas del dibujo (sin recentrar)."""
    bounds = _bounds(layers)
    if bounds is None:
        return None
    center = (bounds[0] + bounds[1]) / 2
    return (float(center[0]), float(center[1]))


def _origin(layers: list[ExportLayer]) -> tuple[float, float, float]:
    """Centro XY del bbox; Z = 0 para conservar las cotas absolutas."""
    center = _center_xy(layers)
    return (*center, 0.0) if center else (0.0, 0.0, 0.0)


def _write(layers: list[ExportLayer], info: SceneInfo, path: Path) -> OutputFile:
    data, stats = build_glb(layers, info)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return OutputFile(path, stats)


def _compress_in_place(output: OutputFile, method: Compression) -> None:
    raw = output.path.with_suffix(".raw.glb")
    output.path.replace(raw)
    try:
        compress(raw, output.path, method)
    except Exception:
        raw.replace(output.path)
        raise
    raw.unlink()
    stats = output.stats
    output.stats = GlbStats(
        output.path.stat().st_size,
        stats.triangles,
        stats.draw_calls,
        stats.instanced_blocks,
        stats.instances,
    )


TileKey = tuple[int, int]


def _write_tiles(
    layers: list[ExportLayer], info: SceneInfo, out_dir: Path, size: float
) -> list[OutputFile]:
    bounds = _bounds(layers)
    if bounds is None:
        return [_write(layers, info, out_dir / MODEL_NAME)]
    x0, y0 = float(bounds[0][0]), float(bounds[0][1])

    def key(x: float, y: float) -> TileKey:
        return (math.floor((x - x0) / size), math.floor((y - y0) / size))

    tiles: dict[TileKey, dict[str, ExportLayer]] = defaultdict(dict)

    def bucket(tile: TileKey, layer: ExportLayer) -> ExportLayer:
        if layer.name not in tiles[tile]:
            tiles[tile][layer.name] = ExportLayer(
                layer.name,
                layer.rule,
                source_entities=layer.source_entities,
                ground_check=layer.ground_check,  # de la capa completa, no de la tesela
            )
        return tiles[tile][layer.name]

    for layer in layers:
        for obj in layer.objects:
            c = _center(obj)
            bucket(key(c[0], c[1]), layer).objects.append(obj)
        for line in layer.lines:
            bucket(key(line.points[0, 0], line.points[0, 1]), layer).lines.append(line)
        for group in layer.groups:
            per_tile: dict[TileKey, list[int]] = defaultdict(list)
            for k, matrix in enumerate(group.matrices):
                per_tile[key(matrix[0, 3], matrix[1, 3])].append(k)
            for tile, picked in per_tile.items():
                bucket(tile, layer).groups.append(
                    InstanceGroup(
                        group.block,
                        group.prototype,
                        group.hard_edges,
                        [group.matrices[k] for k in picked],
                        group.objects_per_instance,
                        [group.object_ids[k] for k in picked] if group.object_ids else [],
                        group.face_parts,
                    )
                )

    outputs = []
    for (i, j), tile_layers in sorted(tiles.items()):
        output = _write(list(tile_layers.values()), info, out_dir / TILES_DIR / f"tile_{i}_{j}.glb")
        output.tile = (i, j)
        output.bbox = _tile_bbox(output.path)
        outputs.append(output)
    return outputs


def _center(obj: SceneObject | LineObject) -> NDArray[np.float64]:
    vertices = obj.mesh.vertices if isinstance(obj, SceneObject) else obj.points
    center: NDArray[np.float64] = (vertices.min(axis=0) + vertices.max(axis=0)) / 2
    return center


def _tile_bbox(path: Path) -> dict[str, list[float]] | None:
    from extrude.gltf_export import decode_glb

    doc, _ = decode_glb(path.read_bytes())
    bbox: dict[str, list[float]] | None = doc["scenes"][0]["extras"]["bbox"]
    return bbox


def _write_manifest(outputs: list[OutputFile], info: SceneInfo, out_dir: Path, size: float) -> None:
    manifest = {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "geometry_revision": info.geometry_revision,
        "origin_offset": list(info.origin),
        "units": "m",
        "crs": info.crs,
        "center_lat": info.center_lat,
        "center_lon": info.center_lon,
        "tile_size": size,
        "tiles": [
            {
                "id": f"{o.tile[0]}_{o.tile[1]}" if o.tile else o.path.stem,
                "file": o.path.relative_to(out_dir).as_posix(),
                "bbox": o.bbox,
                "triangles": o.stats.triangles,
                "size_bytes": o.stats.size_bytes,
            }
            for o in outputs
        ],
    }
    (out_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
