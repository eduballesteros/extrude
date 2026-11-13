"""Altura de cada huella según la regla de su capa (ver prioridad en config.py)."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import shapely
from shapely.geometry import Point, Polygon

from extrude.config import HeightSource, Kind, LayerRule, ValueMeaning
from extrude.model import TextFeature
from extrude.topology import Footprint

ROMAN = re.compile(r"^[IVXLCDM]+$")
# Cota Z de una huella que se considera «sin información» (planos 2D: todo a Z = 0)
NO_ELEVATION_EPS_M = 1e-6
ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


class HeightOrigin(StrEnum):
    ATTRIBUTE = "attribute"
    TEXT = "text"
    ELEVATION = "elevation"
    BLOCK = "block"
    FLOORS = "floors"
    FIXED = "height"
    DEFAULT = "default_height"
    FLAT = "flat"  # viales, agua, vegetación sin altura: superficie plana
    MISSING = "missing"


# Procedencia de una altura para el usuario: lo que el plano acredita, lo que se deduce de
# una cota y lo que es solo un valor de partida que hay que revisar.
VERIFIED_ORIGINS = frozenset(
    {
        HeightOrigin.ATTRIBUTE,
        HeightOrigin.TEXT,
        HeightOrigin.BLOCK,
        HeightOrigin.FLOORS,
        HeightOrigin.FIXED,
    }
)
INFERRED_ORIGINS = frozenset({HeightOrigin.ELEVATION})
PROVISIONAL_ORIGINS = frozenset({HeightOrigin.DEFAULT})


def confidence(origin: HeightOrigin | None) -> str:
    """verified | inferred | provisional | none."""
    if origin in VERIFIED_ORIGINS:
        return "verified"
    if origin in INFERRED_ORIGINS:
        return "inferred"
    if origin in PROVISIONAL_ORIGINS:
        return "provisional"
    return "none"


@dataclass(frozen=True, slots=True)
class HeightResult:
    height: float | None
    origin: HeightOrigin
    ambiguous: bool = False  # varios textos/atributos con valores distintos
    nearby: bool = False  # de un texto fuera del contorno, a ≤ text_tolerance


def parse_number(raw: str) -> float | None:
    """'4' → 4, '12,5' → 12.5, 'IV' → 4 (números romanos, habituales en Catastro)."""
    value = raw.strip().upper()
    if ROMAN.match(value):
        total = 0
        for i, char in enumerate(value):
            current = ROMAN_VALUES[char]
            nxt = ROMAN_VALUES[value[i + 1]] if i + 1 < len(value) else 0
            total += -current if current < nxt else current
        return float(total)
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


class TextIndex:
    """Índice espacial de textos y ATTRIB (en metros) para buscarlos dentro de polígonos."""

    def __init__(self, texts: Sequence[TextFeature], scale: float) -> None:
        self.texts = list(texts)
        self.points = [Point(*(t.position[:2] * scale)) for t in self.texts]
        self.tree = shapely.STRtree(self.points)

    def inside(
        self, footprint: Footprint, neighbors: Neighbors | None = None
    ) -> list[tuple[TextFeature, float]]:
        """Textos interiores o en fachada exterior; nunca patios ni medianeras ambiguas."""
        if not self.texts:
            return []
        anchor = footprint.polygon.representative_point()
        polygon = footprint.polygon
        hits = self.tree.query(polygon, predicate="covers")
        found = []
        for i in hits:
            point = self.points[int(i)]
            if not polygon.contains(point):
                if not polygon.exterior.covers(point):
                    continue  # borde de patio
                if neighbors is not None and not neighbors.belongs_to(point, footprint):
                    continue  # fachada compartida: no asignar a ninguno
            found.append((self.texts[int(i)], anchor.distance(point)))
        return sorted(found, key=lambda pair: pair[1])

    def near(
        self, footprint: Footprint, tolerance: float, neighbors: Neighbors | None = None
    ) -> list[tuple[TextFeature, float]]:
        """Rescate: textos fuera del contorno exterior a ≤ `tolerance` m, por distancia al borde.

        Los puntos de inserción suelen quedar unos centímetros fuera de la fachada. Para no robar
        el rótulo del edificio vecino (medianeras), con `neighbors` se descartan los textos que
        caen dentro de otra huella o que están igual o más cerca de otra. Los de los patios no
        cuentan: están dentro del contorno exterior.
        """
        if not self.texts or tolerance <= 0:
            return []
        hits = self.tree.query(footprint.polygon, predicate="dwithin", distance=tolerance)
        outline = Polygon(footprint.polygon.exterior)
        found = []
        for i in hits:
            point = self.points[int(i)]
            if outline.covers(point):
                continue  # dentro (ya buscado) o en un patio
            if neighbors is not None and not neighbors.belongs_to(point, footprint):
                continue
            found.append((self.texts[int(i)], footprint.polygon.distance(point)))
        return sorted(found, key=lambda pair: pair[1])


class Neighbors:
    """Huellas de una capa, para asignar un texto exterior solo a la más cercana."""

    TIE_M = 1e-6

    def __init__(self, footprints: Sequence[Footprint]) -> None:
        self.polygons = [f.polygon for f in footprints]
        self.index = {id(p): i for i, p in enumerate(self.polygons)}
        self.tree = shapely.STRtree(self.polygons)

    def belongs_to(self, point: Point, footprint: Footprint) -> bool:
        own = self.index.get(id(footprint.polygon))
        if own is None:
            return True
        mine = self.polygons[own].distance(point)
        close = [
            int(i)
            for i in self.tree.query(point, predicate="dwithin", distance=mine + self.TIE_M)
            if int(i) != own
        ]
        if not close:
            return True
        distances = np.array([self.polygons[i].distance(point) for i in close])
        # dentro de otra huella (distancia 0), empate o vecino más cerca: no es de esta
        return not bool((distances <= mine + self.TIE_M).any())


def resolve_height(
    footprint: Footprint,
    rule: LayerRule,
    texts: TextIndex,
    neighbors: Neighbors | None = None,
    base_reference: float | None = None,
) -> HeightResult:
    """`base_reference`: cota absoluta real de la base del objeto (terreno incluido). Solo la
    usa `height_from: elevation`, donde la Z de la huella es la cota absoluta de cubierta."""
    if rule.height_from is not None:
        found = _from_source(footprint, rule, texts, neighbors, base_reference)
        if found is not None:
            return found
    if rule.floors is not None:
        return HeightResult(rule.floors * rule.floor_height, HeightOrigin.FLOORS)
    if rule.height is not None:
        return HeightResult(rule.height, HeightOrigin.FIXED)
    if rule.default_height is not None:
        return HeightResult(rule.default_height, HeightOrigin.DEFAULT)
    if rule.kind is not Kind.BUILDING:
        return HeightResult(0.0, HeightOrigin.FLAT)
    return HeightResult(None, HeightOrigin.MISSING)


def _from_source(
    footprint: Footprint,
    rule: LayerRule,
    texts: TextIndex,
    neighbors: Neighbors | None,
    base_reference: float | None = None,
) -> HeightResult | None:
    match rule.height_from:
        case HeightSource.ATTRIBUTE:
            values, nearby = _attribute_values(footprint, rule, texts, neighbors)
            return _pick(values, rule, HeightOrigin.ATTRIBUTE, nearby)
        case HeightSource.TEXT:
            values, nearby = _text_values(footprint, rule, texts, neighbors)
            return _pick(values, rule, HeightOrigin.TEXT, nearby)
        case HeightSource.BLOCK:
            values = [
                v
                for v in (rule.block_value(s.block_path) for s in footprint.sources)
                if v is not None
            ]
            return _pick(values, rule, HeightOrigin.BLOCK)
        case HeightSource.ELEVATION:
            # Cubierta absoluta menos base absoluta: nunca se suman dos cotas absolutas.
            # Una cubierta en la base o por debajo no da altura: se pasa a la siguiente fuente.
            # Z = 0 es la ausencia de cota de un dibujo 2D, no una cubierta en la cota 0: sobre
            # un terreno negativo daría un edificio tan alto como profundo es el terreno.
            if abs(footprint.elevation) <= NO_ELEVATION_EPS_M:
                return None
            base = rule.base_elevation if base_reference is None else base_reference
            height = footprint.elevation - base
            return HeightResult(height, HeightOrigin.ELEVATION) if height > 1e-6 else None
        case _:
            return None


Found = tuple[list[float], bool]  # valores (el más cercano primero) y si son de rescate
Hits = list[tuple[TextFeature, float]]


def _inside_then_near(
    footprint: Footprint,
    rule: LayerRule,
    texts: TextIndex,
    neighbors: Neighbors | None,
    values_of: Callable[[Hits], list[float]],
) -> Found:
    """Valores de los textos dentro de la huella; si no hay ninguno válido, los de justo fuera
    (a ≤ text_tolerance), ordenados por distancia al borde."""
    values = values_of(texts.inside(footprint, neighbors))
    if values:
        return values, False
    values = values_of(texts.near(footprint, rule.text_tolerance, neighbors))
    return values, bool(values)


def _attribute_values(
    footprint: Footprint, rule: LayerRule, texts: TextIndex, neighbors: Neighbors | None
) -> Found:
    tag = (rule.attribute or "").upper()
    own = [v for s in footprint.sources for t, v in s.attributes if t.upper() == tag]
    if own:
        return [n for n in (parse_number(v) for v in own) if n is not None], False

    def tagged(hits: Hits) -> list[float]:
        raw = [t.text for t, _ in hits if (t.tag or "").upper() == tag]
        return [n for n in (parse_number(v) for v in raw) if n is not None]

    # bloque-etiqueta colocado dentro del polígono (o justo fuera)
    return _inside_then_near(footprint, rule, texts, neighbors, tagged)


def _text_values(
    footprint: Footprint, rule: LayerRule, texts: TextIndex, neighbors: Neighbors | None
) -> Found:
    pattern = re.compile(rule.text_regex)

    def matching(hits: Hits) -> list[float]:
        values = []
        for text, _ in hits:
            if not rule.text_layer_matches(text.layer):
                continue
            match = pattern.search(text.text)
            number = parse_number(match.group(1)) if match else None
            if number is not None:
                values.append(number)
        return values

    return _inside_then_near(footprint, rule, texts, neighbors, matching)


def _pick(
    values: list[float], rule: LayerRule, origin: HeightOrigin, nearby: bool = False
) -> HeightResult | None:
    if not values:
        return None
    value = values[0]  # el más cercano al centro (o al borde, si es un texto de fuera)
    ambiguous = len(set(values)) > 1
    if rule.value_means is ValueMeaning.METERS:
        return HeightResult(value, origin, ambiguous, nearby)
    height = (value + rule.floor_offset) * rule.floor_height
    return HeightResult(height, origin, ambiguous, nearby)
