"""Evidencia por capa para clasificarla: nombre, tipos de entidad y geometría.

Sirve a dos cosas:
- el borrador de configuración (`init_config.py`) decide el `kind` con el nombre cuando es
  reconocible, y si no deja la capa en `ignore`;
- el informe marca como **pendientes de revisión** las capas no convertidas que contienen
  geometría convertible (contornos cerrados, sombreados, mallas), para que una capa entera de
  edificios no quede fuera sin que el usuario lo sepa.

No convierte nada por su cuenta: una capa desconocida con polígonos cerrados puede ser
edificación, parcelas, aceras o un cajetín. La decisión es del usuario (visor → Capas).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from shapely.geometry import Polygon

from extrude.config import Kind
from extrude.model import (
    METERS_PER_UNIT,
    HatchFeature,
    MeshFeature,
    PointFeature,
    PolylineFeature,
    ReadResult,
    TextFeature,
)

# Vocabulario general (español e inglés) por prefijo de cada palabra del nombre de la capa.
KIND_KEYWORDS: tuple[tuple[Kind, tuple[str, ...]], ...] = (
    (Kind.TERRAIN, ("CURVA", "NIVEL", "COTA", "TOPO", "TERREN", "CONTOUR", "TERRAIN")),
    (
        Kind.BUILDING,
        (
            "EDIF",
            "CONSTR",
            "BUILD",
            "MANZ",
            "PARCEL",
            "VIVIEND",
            "BLOQUE",
            "EQUIPAM",
            "TORRE",
            "TOWER",
            "UNIFAMILIAR",
            "PLURIFAMILIAR",
            "ADOSAD",
            "PAREAD",
            "CASA",
            "HOUSE",
            "NAVE",
            "RESIDEN",
            "DOTACION",
            "VOLUM",
            "INMUEBLE",
        ),
    ),
    (
        Kind.ROAD,
        (
            "VIAL",
            "VIARI",
            "CALLE",
            "CALZ",
            "ACERA",
            "ROAD",
            "STREET",
            "CARRET",
            "APARC",
            "PARKING",
            "TRANSPORT",
            "CARRIL",
            "BICI",
            "AVENID",
            "AUTOVI",
            "AUTOPIST",
            "RONDA",
            "ROTOND",
            "ASFALT",
            "PAVIMENT",
            "PEATON",
            "CAMINO",
            "SENDER",
        ),
    ),
    (
        Kind.WATER,
        ("AGUA", "RIO", "LAGO", "LAGUN", "ESTANQ", "WATER", "FUENTE", "PISCIN", "CAUCE"),
    ),
    (Kind.VEGETATION, ("VEGET", "ARBOL", "JARDIN", "VERDE", "PARQUE", "TREE", "CESPED")),
)
MIN_OUTLINE_POINTS = 3


def guess_kind(layer: str) -> Kind:
    """Por prefijo de cada palabra de la capa: 'ZONAS_VERDES' → VERDE, 'MOBILIARIO' ≠ RIO."""
    tokens = [t for t in re.split(r"[\W_]+", layer.upper()) if t]
    for kind, words in KIND_KEYWORDS:
        if any(t.startswith(w) for t in tokens for w in words):
            return kind
    return Kind.IGNORE


@dataclass
class LayerEvidence:
    """Lo que el dibujo contiene en una capa (unidades del dibujo salvo áreas, en m²)."""

    entities: Counter[str] = field(default_factory=Counter)
    closed_outlines: int = 0
    open_polylines: int = 0
    hatches: int = 0
    meshes: int = 0
    points: int = 0
    texts: int = 0
    block_inserts: int = 0
    z_values: set[float] = field(default_factory=set)
    areas_m2: list[float] = field(default_factory=list)
    _inserts: set[str] = field(default_factory=set)

    @property
    def convertible(self) -> int:
        """Elementos que podrían dar geometría 3D si la capa se clasificara."""
        return self.closed_outlines + self.hatches + self.meshes

    @property
    def has_elevation(self) -> bool:
        return any(abs(z) > 0 for z in self.z_values)

    def as_dict(self) -> dict[str, Any]:
        areas = sorted(self.areas_m2)
        z = sorted(self.z_values)
        return {
            "closed_outlines": self.closed_outlines,
            "open_polylines": self.open_polylines,
            "hatches": self.hatches,
            "meshes": self.meshes,
            "points": self.points,
            "texts": self.texts,
            "block_inserts": self.block_inserts,
            "z_min": z[0] if z else None,
            "z_max": z[-1] if z else None,
            "distinct_z": len(z),
            "median_area_m2": round(areas[len(areas) // 2], 2) if areas else None,
        }


def layer_evidence(read: ReadResult) -> dict[str, LayerEvidence]:
    scale = METERS_PER_UNIT[read.units]
    layers: dict[str, LayerEvidence] = {}
    for feature in read.features:
        info = layers.setdefault(feature.layer, LayerEvidence())
        info.entities[feature.source.dxftype] += 1
        insert = feature.source.insert
        if insert is not None and insert.handle not in info._inserts:
            info._inserts.add(insert.handle)
            info.block_inserts += 1
        if isinstance(feature, PointFeature):
            info.points += 1
            info.z_values.add(round(float(feature.position[2]), 6))
        elif isinstance(feature, TextFeature):
            info.texts += 1
        elif isinstance(feature, HatchFeature):
            info.hatches += 1
        elif isinstance(feature, MeshFeature):
            info.meshes += 1
            info.z_values.update(np.round(feature.vertices[:, 2], 6).tolist())
        elif isinstance(feature, PolylineFeature):
            info.z_values.update(np.round(np.unique(feature.points[:, 2]), 6).tolist())
            if feature.closed and len(feature.points) >= MIN_OUTLINE_POINTS:
                polygon = Polygon(feature.points[:, :2] * scale)
                if polygon.is_valid and polygon.area > 0:
                    info.closed_outlines += 1
                    info.areas_m2.append(float(polygon.area))
                    continue
            info.open_polylines += 1
    return layers


def describe_pending(name: str, evidence: LayerEvidence) -> dict[str, Any]:
    """Ficha de una capa no convertida con geometría convertible, para revisarla en el visor."""
    parts = []
    if evidence.closed_outlines:
        parts.append(f"{evidence.closed_outlines} contornos cerrados")
    if evidence.hatches:
        parts.append(f"{evidence.hatches} sombreados")
    if evidence.meshes:
        parts.append(f"{evidence.meshes} mallas 3D")
    guess = guess_kind(name)
    return {
        "name": name,
        "evidence": evidence.as_dict(),
        "summary": ", ".join(parts),
        # Sugerencia por el nombre; None = no hay base para proponer un tipo
        "suggested_kind": None if guess is Kind.IGNORE else guess.value,
    }
