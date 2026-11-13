"""Borrador de configuración YAML a partir de un dibujo concreto (`extrude init-config`).

Es una propuesta para revisar, no una verdad: adivina el `kind` por el nombre de la capa y la
fuente de altura según lo que encuentre (atributos, textos con número, cotas Z). Cada regla
lleva comentarios con la evidencia usada.

Política de cotas (general, no depende de ningún plano concreto):
- La base de lo que se extruye se deja en `base_auto`: se decide al convertir, por objeto, con
  el terreno realmente triangulable (no con el nombre de una capa).
- Una cota Z en una huella no demuestra por sí sola ni base ni cubierta ni altura. Solo se
  propone como altura (`height_from: elevation`) cuando no hay terreno ni otra fuente.
- Sin fuente de altura fiable se usa `default_height`, que es PROVISIONAL y así se informa.
- Una capa de nombre no reconocido queda en `ignore`; si tiene geometría convertible se
  anota como pendiente de revisión (el informe y el visor la muestran).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import shapely
import yaml
from numpy.typing import NDArray
from shapely.geometry import Point, Polygon

from extrude.build import terrain_from_samples
from extrude.classification import guess_kind
from extrude.config import (
    DEFAULT_MATERIALS,
    DEFAULT_TOLERANCE_M,
    EXTRUDED_KINDS,
    Kind,
    parse_config,
)
from extrude.model import (
    METERS_PER_UNIT,
    HatchFeature,
    MeshFeature,
    PointFeature,
    PolylineFeature,
    ReadResult,
    TextFeature,
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
    hatches: int = 0
    meshes: int = 0
    # Muestras acotadas (curvas, puntos y vértices de mallas) por si la capa es terreno
    samples: list[NDArray[np.float64]] = field(default_factory=list)

    @property
    def convertible(self) -> int:
        return len(self.outlines) + self.hatches + self.meshes


def _glob_escape(layer: str) -> str:
    """Nombre literal de capa como patrón glob (escapa * ? [)."""
    return re.sub(r"([*?[])", r"[\1]", layer)


def _collect(read: ReadResult) -> tuple[dict[str, _LayerInfo], list[TextFeature]]:
    layers: dict[str, _LayerInfo] = defaultdict(_LayerInfo)
    texts: list[TextFeature] = []
    for f in read.features:
        if isinstance(f, PointFeature):
            layers[f.layer].has_points = True
            layers[f.layer].geometry["POINT"] += 1
            layers[f.layer].samples.append(f.position.reshape(1, 3))
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
            if isinstance(f, HatchFeature):
                info.hatches += 1
            if isinstance(f, MeshFeature):
                info.meshes += 1
                info.samples.append(f.vertices)
                if abs(f.vertices[:, 2]).max() > 0:
                    info.has_elevation = True
            if isinstance(f, PolylineFeature):
                info.samples.append(f.points)
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
    elif info.has_elevation and not has_terrain:
        rule["height_from"] = "elevation"
        notes.append(
            "Las polilíneas tienen cota Z y no hay terreno ni otra fuente de altura: se "
            "interpreta como cota de cubierta sobre la cota 0 (INFERENCIA: compruébalo)."
        )
    elif info.has_elevation:
        notes.append(
            "Con terreno, la cota Z de una huella no demuestra una altura de edificio: "
            "se usa default_height PROVISIONAL; revisa plantas o altura."
        )
    else:
        notes.append("No se encontró altura en el dibujo: se usa default_height (PROVISIONAL).")
    rule["floor_height"] = 3.0
    rule["default_height"] = DEFAULT_BUILDING_HEIGHT_M
    return rule, notes


def _material(kind: Kind) -> dict[str, Any]:
    m = DEFAULT_MATERIALS[kind]
    return {"color": m.color, "roughness": m.roughness, "metalness": m.metalness}


def draft_config(read: ReadResult, display_name: str | None = None) -> str:
    layers, texts = _collect(read)
    # Terreno: solo cuenta si de verdad se puede triangular con cotas (no por el nombre)
    named_terrain = [name for name in sorted(layers) if guess_kind(name) is Kind.TERRAIN]
    surface, terrain_info = terrain_from_samples(
        [s for name in named_terrain for s in layers[name].samples],
        METERS_PER_UNIT[read.units],
        DEFAULT_TOLERANCE_M,
    )
    has_terrain = surface is not None
    terrain_layers = named_terrain if has_terrain else []
    blocks: list[str] = []
    for layer, info in sorted(layers.items()):
        kind = guess_kind(layer)
        counts = ", ".join(f"{t}×{n}" for t, n in sorted(info.geometry.items()))
        notes = [f"Capa {layer}: {counts}"]
        if kind is Kind.BUILDING:
            rule, extra = _building_rule(layer, info, texts, has_terrain=has_terrain)
            notes += extra
        else:
            if kind is Kind.TERRAIN and not (info.has_elevation or info.has_points):
                kind = Kind.IGNORE
                notes.append("Parece terreno pero no tiene cotas Z: no sirve para el TIN.")
            elif kind is Kind.TERRAIN and not has_terrain:
                notes.append(
                    f"Parece terreno, pero no forma una superficie utilizable: "
                    f"{terrain_info['reason']}. Nada se apoyará en él."
                )
            rule = {"match": _glob_escape(layer), "kind": kind.value}
            if kind in KIND_DEFAULT_HEIGHT:
                rule["height"] = KIND_DEFAULT_HEIGHT[kind]
            if kind is Kind.VEGETATION and info.blocks:
                rule["height"] = TREE_BLOCK_HEIGHT_M
                rule["detect_holes"] = False
                listed = ", ".join(f"{n} ×{c}" for n, c in info.blocks.most_common())
                notes.append(f"Bloques de árbol ({listed}): volumen de {TREE_BLOCK_HEIGHT_M:g} m.")
            if kind is Kind.IGNORE and guess_kind(layer) is Kind.IGNORE:
                if info.convertible:
                    notes.append(
                        f"PENDIENTE DE REVISIÓN: nombre no reconocido, pero contiene "
                        f"{info.convertible} elementos convertibles (contornos cerrados, "
                        "sombreados o mallas). Decide su tipo en el visor (Capas) o aquí."
                    )
                else:
                    notes.append("Nombre no reconocido: revisa si debe ser building/road/water/…")
        if kind in EXTRUDED_KINDS:
            # La base se decide al convertir, por objeto, con la evidencia real (ver build.py)
            rule["base_auto"] = True
            if has_terrain:
                on = ", ".join(terrain_layers)
                if kind is Kind.BUILDING and info.has_elevation:
                    notes.append(
                        "Apoyo automático: la cota Z de las huellas no garantiza una base "
                        f"fiable; cada objeto se apoya en el terreno ({on})."
                    )
                else:
                    notes.append(f"Apoyo automático: cada objeto se apoya en el terreno ({on}).")
            elif info.has_elevation and rule.get("height_from") != "elevation":
                notes.append(
                    "Apoyo automático: no hay terreno utilizable; la base de cada objeto es la "
                    "cota Z de su huella (INFERENCIA) o la cota 0 si no tiene."
                )
            else:
                notes.append("Apoyo automático: no hay terreno utilizable; la base es la cota 0.")
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


_guess_kind = guess_kind  # nombre anterior (pruebas y código externo)
