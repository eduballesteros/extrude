"""Modelo intermedio: geometría DXF ya resuelta a WCS (float64, unidades del dibujo)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

Points = NDArray[np.float64]  # (N, 3)


class LengthUnit(StrEnum):
    MM = "mm"
    CM = "cm"
    M = "m"
    KM = "km"
    IN = "in"
    FT = "ft"


METERS_PER_UNIT: dict[LengthUnit, float] = {
    LengthUnit.MM: 0.001,
    LengthUnit.CM: 0.01,
    LengthUnit.M: 1.0,
    LengthUnit.KM: 1000.0,
    LengthUnit.IN: 0.0254,
    LengthUnit.FT: 0.3048,
}

# Códigos de $INSUNITS soportados (0 = sin unidades)
INSUNITS_CODES: dict[int, LengthUnit] = {
    1: LengthUnit.IN,
    2: LengthUnit.FT,
    4: LengthUnit.MM,
    5: LengthUnit.CM,
    6: LengthUnit.M,
    7: LengthUnit.KM,
}


@dataclass(frozen=True, slots=True)
class Provenance:
    """De dónde sale una entidad: tipo DXF, handle y cadena de bloques que la contienen."""

    dxftype: str
    handle: str | None
    block_path: tuple[str, ...] = ()
    attributes: tuple[tuple[str, str], ...] = ()  # (tag, valor) de los INSERT padres


@dataclass(frozen=True, slots=True)
class PolylineFeature:
    layer: str
    points: Points
    closed: bool
    source: Provenance


@dataclass(frozen=True, slots=True)
class HatchFeature:
    layer: str
    loops: tuple[Points, ...]  # contornos cerrados; los huecos se deciden en topología
    source: Provenance


@dataclass(frozen=True, slots=True)
class MeshFeature:
    layer: str
    vertices: Points
    faces: tuple[tuple[int, ...], ...]
    source: Provenance


@dataclass(frozen=True, slots=True)
class TextFeature:
    layer: str
    text: str
    position: Points  # (3,)
    source: Provenance
    tag: str | None = None  # solo ATTRIB


@dataclass(frozen=True, slots=True)
class PointFeature:
    layer: str
    position: Points  # (3,)
    source: Provenance


Feature = PolylineFeature | HatchFeature | MeshFeature | TextFeature | PointFeature


@dataclass(frozen=True, slots=True)
class BBox:
    min: tuple[float, float, float]
    max: tuple[float, float, float]

    @property
    def size(self) -> tuple[float, float, float]:
        return (
            self.max[0] - self.min[0],
            self.max[1] - self.min[1],
            self.max[2] - self.min[2],
        )

    @staticmethod
    def of(points: Points) -> BBox:
        lo = points.min(axis=0)
        hi = points.max(axis=0)
        return BBox(
            (float(lo[0]), float(lo[1]), float(lo[2])), (float(hi[0]), float(hi[1]), float(hi[2]))
        )


LayerType = tuple[str, str]  # (capa, tipo DXF)


@dataclass
class ReadResult:
    source: Path
    dxf_version: str
    insunits_code: int
    units: LengthUnit
    units_assumed: bool
    layers: list[str]
    features: list[Feature] = field(default_factory=list)
    read: Counter[LayerType] = field(default_factory=Counter)
    unsupported: Counter[LayerType] = field(default_factory=Counter)
    skipped: Counter[LayerType] = field(default_factory=Counter)
    failed: Counter[LayerType] = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)
    bbox: BBox | None = None
