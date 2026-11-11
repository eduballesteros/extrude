"""Borrador de configuración YAML a partir de un dibujo concreto (`extrude init-config`).

Es una propuesta para revisar a mano, no una verdad: adivina el `kind` por el nombre de la capa
y la fuente de altura según lo que encuentre (atributos, textos con número, cotas Z). Cada regla
lleva comentarios con la evidencia usada.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

import shapely
import yaml
from shapely.geometry import Point, Polygon

from extrude.config import DEFAULT_MATERIALS, Kind, parse_config
from extrude.model import (
    HatchFeature,
    MeshFeature,
    PointFeature,
    PolylineFeature,
    ReadResult,
    TextFeature,
)

KIND_KEYWORDS: tuple[tuple[Kind, tuple[str, ...]], ...] = (
    (Kind.TERRAIN, ("CURVA", "NIVEL", "COTA", "TOPO", "TERREN", "CONTOUR", "TERRAIN")),
    (Kind.BUILDING, ("EDIF", "CONSTR", "BUILD", "MANZ", "PARCEL", "VIVIEND", "BLOQUE", "EQUIPAM")),
    (Kind.ROAD, ("VIAL", "CALLE", "CALZ", "ACERA", "ROAD", "STREET", "CARRET", "APARC")),
    (Kind.WATER, ("AGUA", "RIO", "LAGO", "WATER", "FUENTE", "PISCIN", "CAUCE")),
    (Kind.VEGETATION, ("VEGET", "ARBOL", "JARDIN", "VERDE", "PARQUE", "TREE", "CESPED")),
)
HEIGHT_ATTRIBUTE = re.compile(r"PLANT|ALTUR|NIVEL|FLOOR|STOREY|HEIGHT|N_?PL", re.IGNORECASE)
DEFAULT_BUILDING_HEIGHT_M = 9.0
DEFAULT_BLOCK_FLOORS = 3
MIN_OUTLINE_POINTS = 3
WEAK_PATTERN_MIN_SHARE = 0.5
TREE_BLOCK_HEIGHT_M = 6.0
KIND_DEFAULT_HEIGHT: dict[Kind, float] = {Kind.ROAD: 0.05, Kind.WATER: 0.02, Kind.VEGETATION: 0.1}


@dataclass(frozen=True)
class TextPattern:
    regex: str
    meaning: str
    floor_offset: int
    description: str
    weak: bool = False  # solo si aparece en buena parte de los contornos


# Orden: de más específico a más genérico
TEXT_PATTERNS: tuple[TextPattern, ...] = (
    TextPattern(r"B\s*\+\s*(\d+)", "floors", 1, "baja + n plantas (B+3 → 4 plantas)"),
    TextPattern(r"^\s*([IVXL]+)\b", "floors", 0, "plantas en números romanos (Catastro: IV)"),
    TextPattern(r"(\d+)\s*P\b", "floors", 0, "número de plantas (4P)"),
    TextPattern(r"(\d+(?:[.,]\d+)?)\s*m\b", "meters", 0, "altura en metros (12.5m)"),
    TextPattern(
        r"^\s*(\d{1,2})\s*$", "floors", 0, "número suelto (posible nº de plantas)", weak=True
    ),
)


@dataclass
class _LayerInfo:
    geometry: Counter[str] = field(default_factory=Counter)
    attribute_tags: set[str] = field(default_factory=set)
    has_elevation: bool = False
    blocks: Counter[str] = field(default_factory=Counter)
    seen_inserts: set[str] = field(default_factory=set)
    has_points: bool = False
    outlines: list[Polygon] = field(default_factory=list)


def _glob_escape(layer: str) -> str:
    """Nombre literal de capa como patrón glob (escapa * ? [)."""
    return re.sub(r"([*?[])", r"[\1]", layer)


def _guess_kind(layer: str) -> Kind:
    """Por prefijo de cada palabra de la capa: 'ZONAS_VERDES' → VERDE, 'MOBILIARIO' ≠ RIO."""
    tokens = [t for t in re.split(r"[\W_]+", layer.upper()) if t]
    for kind, words in KIND_KEYWORDS:
        if any(t.startswith(w) for t in tokens for w in words):
            return kind
    return Kind.IGNORE


def _collect(read: ReadResult) -> tuple[dict[str, _LayerInfo], list[TextFeature]]:
    layers: dict[str, _LayerInfo] = defaultdict(_LayerInfo)
    texts: list[TextFeature] = []
    for f in read.features:
        if isinstance(f, PointFeature):
            layers[f.layer].has_points = True
            layers[f.layer].geometry["POINT"] += 1
            continue
        if isinstance(f, TextFeature):
            texts.append(f)
            if f.tag:
                layers[f.layer].attribute_tags.add(f.tag)
            continue
        if isinstance(f, PolylineFeature | HatchFeature | MeshFeature):
            info = layers[f.layer]
            info.geometry[f.source.dxftype] += 1
            info.attribute_tags.update(tag for tag, _ in f.source.attributes)
            insert = f.source.insert
            if (
                f.source.block_path
                and insert is not None
                and insert.handle not in info.seen_inserts
            ):
                info.seen_inserts.add(insert.handle)
                info.blocks[f.source.block_path[-1]] += 1
            if isinstance(f, PolylineFeature):
                if abs(f.points[:, 2]).max() > 0:
                    info.has_elevation = True
                if f.closed and len(f.points) >= MIN_OUTLINE_POINTS:
                    polygon = Polygon(f.points[:, :2])
                    if polygon.is_valid and polygon.area > 0:
                        info.outlines.append(polygon)
    return {k: v for k, v in layers.items() if v.geometry}, texts


def _texts_inside(info: _LayerInfo, texts: list[TextFeature]) -> dict[str, list[str]]:
    """Textos que caen dentro de algún contorno de la capa (los rótulos de altura lo hacen;
    leyendas, cotas y títulos no), agrupados por capa del texto."""
    if not info.outlines:
        return {}
    tree = shapely.STRtree(info.outlines)
    inside: dict[str, list[str]] = defaultdict(list)
    for text in texts:
        if len(tree.query(Point(text.position[:2]), predicate="within")):
            inside[text.layer].append(text.text)
    return inside


def _best_text_pattern(
    texts: dict[str, list[str]], outlines: int
) -> tuple[TextPattern, list[str], int] | None:
    best: tuple[TextPattern, list[str], int] | None = None
    for pattern in TEXT_PATTERNS:
        regex = re.compile(pattern.regex)
        hits = {
            layer: sum(bool(regex.search(t)) for t in values) for layer, values in texts.items()
        }
        layers = sorted(layer for layer, n in hits.items() if n)
        total = sum(hits.values())
        if pattern.weak and total < WEAK_PATTERN_MIN_SHARE * outlines:
            continue  # p. ej. números de leyenda o identificadores, no plantas
        if total and (best is None or total > best[2]):
            best = (pattern, layers, total)
    return best


def _building_rule(
    layer: str, info: _LayerInfo, all_texts: list[TextFeature], *, has_terrain: bool = False
) -> tuple[dict[str, Any], list[str]]:
    texts = _texts_inside(info, all_texts)
    rule: dict[str, Any] = {"match": _glob_escape(layer), "kind": Kind.BUILDING.value}
    notes: list[str] = []
    height_tags = sorted(t for t in info.attribute_tags if HEIGHT_ATTRIBUTE.search(t))
    text_choice = _best_text_pattern(texts, len(info.outlines))
    if height_tags:
        rule |= {"height_from": "attribute", "attribute": height_tags[0], "value_means": "floors"}
        notes.append(f"Atributos de bloque encontrados: {', '.join(sorted(info.attribute_tags))}")
        notes.append("Si el atributo es la altura en metros, cambia value_means a meters.")
    elif text_choice is not None:
        pattern, text_layers, count = text_choice
        rule |= {
            "height_from": "text",
            "text_regex": pattern.regex,
            "text_layers": text_layers,
            "value_means": pattern.meaning,
        }
        if pattern.floor_offset:
            rule["floor_offset"] = pattern.floor_offset
        samples = [t for layer in text_layers for t in texts[layer]][:5]
        notes.append(f"{count} textos parecen {pattern.description}: {' | '.join(samples)}")
    elif info.blocks:
        rule["height_from"] = "block"
        rule["block_values"] = {_glob_escape(n): DEFAULT_BLOCK_FLOORS for n in sorted(info.blocks)}
        listed = ", ".join(f"{n} ×{c}" for n, c in info.blocks.most_common())
        notes.append(f"Los edificios son bloques ({listed}).")
        notes.append("AJUSTA block_values: plantas de cada tipo de bloque (valor provisional).")
    elif info.has_elevation:
        rule["height_from"] = "elevation"
        notes.append("Las polilíneas tienen cota Z: se usa como altura de coronación.")
    else:
        notes.append("No se encontró altura en el dibujo: se usa default_height.")
    if info.has_elevation and rule.get("height_from") != "elevation" and not has_terrain:
        rule["base_from_elevation"] = True
        notes.append("Las plantas tienen cota Z: se usa como cota de la base de cada edificio.")
    rule["floor_height"] = 3.0
    rule["default_height"] = DEFAULT_BUILDING_HEIGHT_M
    return rule, notes


def _material(kind: Kind) -> dict[str, Any]:
    m = DEFAULT_MATERIALS[kind]
    return {"color": m.color, "roughness": m.roughness, "metalness": m.metalness}


def draft_config(read: ReadResult, display_name: str | None = None) -> str:
    layers, texts = _collect(read)
    terrain_layers = [
        name
        for name, info in sorted(layers.items())
        if _guess_kind(name) is Kind.TERRAIN and (info.has_elevation or info.has_points)
    ]
    blocks: list[str] = []
    for layer, info in sorted(layers.items()):
        kind = _guess_kind(layer)
        counts = ", ".join(f"{t}×{n}" for t, n in sorted(info.geometry.items()))
        notes = [f"Capa {layer}: {counts}"]
        if kind is Kind.BUILDING:
            rule, extra = _building_rule(layer, info, texts, has_terrain=bool(terrain_layers))
            notes += extra
        else:
            if kind is Kind.TERRAIN and not (info.has_elevation or info.has_points):
                kind = Kind.IGNORE
                notes.append("Parece terreno pero no tiene cotas Z: no sirve para el TIN.")
            rule = {"match": _glob_escape(layer), "kind": kind.value}
            if kind in KIND_DEFAULT_HEIGHT:
                rule["height"] = KIND_DEFAULT_HEIGHT[kind]
            if kind is Kind.VEGETATION and info.blocks:
                rule["height"] = TREE_BLOCK_HEIGHT_M
                rule["detect_holes"] = False
                listed = ", ".join(f"{n} ×{c}" for n, c in info.blocks.most_common())
                notes.append(f"Bloques de árbol ({listed}): volumen de {TREE_BLOCK_HEIGHT_M:g} m.")
            if kind is Kind.IGNORE:
                notes.append("Nombre no reconocido: revisa si debe ser building/road/water/…")
        if (
            terrain_layers
            and kind not in (Kind.IGNORE, Kind.TERRAIN, Kind.LINE)
            and not rule.get("base_from_elevation")
            and rule.get("height_from") != "elevation"
        ):
            rule["base_from_terrain"] = True
            on = ", ".join(terrain_layers)
            if kind is Kind.BUILDING and info.has_elevation:
                notes.append(
                    "La presencia de cotas Z no garantiza bases fiables en toda la capa: "
                    f"se apoya en el terreno ({on})."
                )
            else:
                notes.append(f"Sin cota de base propia: se apoya en el terreno ({on}).")
        if kind is not Kind.IGNORE:
            rule["material"] = _material(kind)
        blocks.append(_render_rule(rule, notes))

    text_only = sorted({t.layer for t in texts} - set(layers))
    blocks.append(
        _render_rule(
            {"match": "*", "kind": "ignore"},
            ["Resto de capas (textos, cotas, anotaciones…)"]
            + ([f"Capas solo de texto: {', '.join(text_only)}"] if text_only else []),
        )
    )
    units = "auto" if not read.units_assumed else "m"
    header = [
        f"# Borrador generado por `extrude init-config` para {display_name or read.source.name}",
        "# REVÍSALO: kinds y fuentes de altura son suposiciones. Opciones: docs/CONFIG.md",
        "# Prioridad de altura: height_from → floors×floor_height → height → default_height",
        "",
        f"units: {units}"
        + ("            # $INSUNITS ausente: comprueba la escala" if read.units_assumed else ""),
        "recenter: true",
        "tolerance: 0.01",
        "",
        "layers:",
    ]
    document = "\n".join(header) + "\n" + "\n".join(blocks)
    parse_config(yaml.safe_load(document), source="borrador")  # el borrador debe ser válido
    return document


def _render_rule(rule: dict[str, Any], notes: list[str]) -> str:
    body = yaml.safe_dump([rule], sort_keys=False, allow_unicode=True, width=200)
    comments = "".join(f"  # {n}\n" for n in notes)
    return comments + "".join(f"  {line}\n" for line in body.splitlines())
