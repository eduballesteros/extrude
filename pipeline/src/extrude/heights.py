"""Altura de cada huella según la regla de su capa (ver prioridad en config.py)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import shapely
from shapely.geometry import Point

from extrude.config import HeightSource, Kind, LayerRule, ValueMeaning
from extrude.model import TextFeature
from extrude.topology import Footprint

ROMAN = re.compile(r"^[IVXLCDM]+$")
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


@dataclass(frozen=True, slots=True)
class HeightResult:
    height: float | None
    origin: HeightOrigin
    ambiguous: bool = False  # varios textos/atributos con valores distintos


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

    def inside(self, footprint: Footprint) -> list[tuple[TextFeature, float]]:
        """Textos dentro de la huella, con su distancia al punto representativo."""
        if not self.texts:
            return []
        anchor = footprint.polygon.representative_point()
        hits = self.tree.query(footprint.polygon, predicate="contains")
        found = [(self.texts[int(i)], anchor.distance(self.points[int(i)])) for i in hits]
        return sorted(found, key=lambda pair: pair[1])


def resolve_height(footprint: Footprint, rule: LayerRule, texts: TextIndex) -> HeightResult:
    if rule.height_from is not None:
        found = _from_source(footprint, rule, texts)
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


def _from_source(footprint: Footprint, rule: LayerRule, texts: TextIndex) -> HeightResult | None:
    match rule.height_from:
        case HeightSource.ATTRIBUTE:
            values = _attribute_values(footprint, rule, texts)
            return _pick(values, rule, HeightOrigin.ATTRIBUTE)
        case HeightSource.TEXT:
            return _pick(_text_values(footprint, rule, texts), rule, HeightOrigin.TEXT)
        case HeightSource.BLOCK:
            values = [
                v
                for v in (rule.block_value(s.block_path) for s in footprint.sources)
                if v is not None
            ]
            return _pick(values, rule, HeightOrigin.BLOCK)
        case HeightSource.ELEVATION:
            height = footprint.elevation - rule.base_elevation
            return HeightResult(height, HeightOrigin.ELEVATION) if height > 0 else None
        case _:
            return None


def _attribute_values(footprint: Footprint, rule: LayerRule, texts: TextIndex) -> list[float]:
    tag = (rule.attribute or "").upper()
    own = [v for s in footprint.sources for t, v in s.attributes if t.upper() == tag]
    if not own:  # bloque-etiqueta colocado dentro del polígono
        own = [t.text for t, _ in texts.inside(footprint) if (t.tag or "").upper() == tag]
    return [n for n in (parse_number(v) for v in own) if n is not None]


def _text_values(footprint: Footprint, rule: LayerRule, texts: TextIndex) -> list[float]:
    pattern = re.compile(rule.text_regex)
    values = []
    for text, _ in texts.inside(footprint):
        if not rule.text_layer_matches(text.layer):
            continue
        match = pattern.search(text.text)
        number = parse_number(match.group(1)) if match else None
        if number is not None:
            values.append(number)
    return values


def _pick(values: list[float], rule: LayerRule, origin: HeightOrigin) -> HeightResult | None:
    if not values:
        return None
    value = values[0]  # el más cercano al centro de la huella
    ambiguous = len(set(values)) > 1
    if rule.value_means is ValueMeaning.METERS:
        return HeightResult(value, origin, ambiguous)
    return HeightResult((value + rule.floor_offset) * rule.floor_height, origin, ambiguous)
