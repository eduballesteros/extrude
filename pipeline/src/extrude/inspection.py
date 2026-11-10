"""Resumen de un DXF para `extrude inspect`: lo necesario para escribir el YAML de capas."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from extrude.model import METERS_PER_UNIT, PointFeature, ReadResult, TextFeature

MAX_TEXT_SAMPLES = 5
LARGE_COORDINATE_M = 100_000.0


@dataclass
class LayerSummary:
    name: str
    entities: dict[str, int] = field(default_factory=dict)
    unsupported: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    failed: dict[str, int] = field(default_factory=dict)
    text_samples: list[str] = field(default_factory=list)
    attribute_tags: list[str] = field(default_factory=list)
    has_elevation: bool = False


@dataclass
class InspectSummary:
    file: str
    dxf_version: str
    units: str
    units_assumed: bool
    insunits_code: int
    bbox_min: list[float] | None
    bbox_max: list[float] | None
    bbox_size_m: list[float] | None
    totals: dict[str, int]
    layers: list[LayerSummary]
    empty_layers: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize(result: ReadResult) -> InspectSummary:
    layers: dict[str, LayerSummary] = {}

    def layer(name: str) -> LayerSummary:
        return layers.setdefault(name, LayerSummary(name))

    buckets = (
        ("entities", result.read),
        ("unsupported", result.unsupported),
        ("skipped", result.skipped),
        ("failed", result.failed),
    )
    for attr, counter in buckets:
        for (name, dxftype), count in counter.items():
            getattr(layer(name), attr)[dxftype] = count

    texts: dict[str, list[str]] = defaultdict(list)
    tags: dict[str, set[str]] = defaultdict(set)
    for feature in result.features:
        if isinstance(feature, TextFeature):
            if feature.tag:
                tags[feature.layer].add(feature.tag)
            if feature.text and feature.text not in texts[feature.layer]:
                texts[feature.layer].append(feature.text)
        elif not isinstance(feature, PointFeature):
            continue
        for tag, _ in feature.source.attributes:
            tags[feature.layer].add(tag)
    for feature in result.features:
        points = getattr(feature, "points", None)
        if points is not None and abs(points[:, 2]).max() > 0:
            layer(feature.layer).has_elevation = True

    for name, samples in texts.items():
        layer(name).text_samples = samples[:MAX_TEXT_SAMPLES]
    for name, names in tags.items():
        layer(name).attribute_tags = sorted(names)

    warnings = list(result.warnings)
    scale = METERS_PER_UNIT[result.units]
    bbox = result.bbox
    if (
        bbox is not None
        and max(abs(c) for c in (*bbox.min, *bbox.max)) * scale > LARGE_COORDINATE_M
    ):
        warnings.append(
            "Coordenadas grandes (probablemente UTM): se recentrarán al exportar y el "
            "desplazamiento se guardará en scene.extras.origin_offset."
        )
    if result.unsupported:
        total = sum(result.unsupported.values())
        warnings.append(
            f"{total} entidades ACIS (3DSOLID/BODY/REGION/SURFACE) no se convertirán. "
            "Para incluirlas, conviértelas a malla en AutoCAD (CONVTOMESH o MESHSMOOTH)."
        )

    totals: Counter[str] = Counter()
    for (_, dxftype), count in result.read.items():
        totals[dxftype] += count

    return InspectSummary(
        file=str(result.source),
        dxf_version=result.dxf_version,
        units=result.units.value,
        units_assumed=result.units_assumed,
        insunits_code=result.insunits_code,
        bbox_min=list(bbox.min) if bbox else None,
        bbox_max=list(bbox.max) if bbox else None,
        bbox_size_m=[s * scale for s in bbox.size] if bbox else None,
        totals=dict(totals.most_common()),
        layers=sorted(layers.values(), key=lambda s: s.name),
        empty_layers=[n for n in result.layers if n not in layers],
        warnings=warnings,
    )


def format_text(summary: InspectSummary) -> str:
    lines = [
        f"Archivo:   {summary.file}",
        f"Versión:   {summary.dxf_version}",
        f"Unidades:  {summary.units}"
        + (" (supuestas)" if summary.units_assumed else "")
        + f"  [$INSUNITS={summary.insunits_code}]",
    ]
    if summary.bbox_min and summary.bbox_max and summary.bbox_size_m:
        lo, hi, size = summary.bbox_min, summary.bbox_max, summary.bbox_size_m
        lines.append(f"BBox min:  ({lo[0]:.3f}, {lo[1]:.3f}, {lo[2]:.3f})")
        lines.append(f"BBox max:  ({hi[0]:.3f}, {hi[1]:.3f}, {hi[2]:.3f})")
        lines.append(f"Tamaño:    {size[0]:.1f} × {size[1]:.1f} × {size[2]:.1f} m")
    else:
        lines.append("BBox:      (sin geometría)")
    lines.append("")
    lines.append("Entidades por tipo:")
    lines.extend(f"  {t:<14} {n:>8}" for t, n in summary.totals.items())
    lines.append("")
    lines.append("Capas:")
    for layer in summary.layers:
        counts = ", ".join(f"{t}×{n}" for t, n in sorted(layer.entities.items()))
        lines.append(f"  {layer.name}: {counts}")
        for label, values in (
            ("no soportadas", layer.unsupported),
            ("omitidas", layer.skipped),
            ("fallidas", layer.failed),
        ):
            if values:
                detail = ", ".join(f"{t}×{n}" for t, n in sorted(values.items()))
                lines.append(f"      {label}: {detail}")
        if layer.text_samples:
            lines.append(f"      textos: {' | '.join(layer.text_samples)}")
        if layer.attribute_tags:
            lines.append(f"      atributos: {', '.join(layer.attribute_tags)}")
        if layer.has_elevation:
            lines.append("      tiene cotas Z distintas de 0")
    if summary.empty_layers:
        lines.append(f"  (vacías: {', '.join(summary.empty_layers)})")
    if summary.warnings:
        lines.append("")
        lines.append("Avisos:")
        lines.extend(f"  - {w}" for w in summary.warnings)
    return "\n".join(lines)
