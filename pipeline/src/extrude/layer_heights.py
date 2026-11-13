"""Ajustes por capa hechos desde el visor: editan el YAML del proyecto sin perder lo escrito a mano.

Tres ajustes, combinables por capa:
- `height`: altura fija en metros para todos los elementos de la capa;
- `support: "auto"`: el apoyo pasa a decidirlo la política general (`base_auto`), la misma que
  usa una importación nueva; así se reparan proyectos antiguos con la base fija en 0;
- `kind`: clasifica una capa que estaba ignorada o sin regla (o la marca como revisada si se
  decide ignorarla);
- `z_height: "ignore"`: deja de tomar la cota Z de la huella como altura (`height_from:
  elevation`). Lo añade el servidor al reparar el apoyo de un borrador generado: esa regla era
  una suposición del borrador antiguo, y con terreno una Z no acredita una altura.

Viven en un bloque gestionado al principio de `layers:` (entre dos comentarios de marca). Cada
entrada es una regla que encaja EXACTAMENTE con una capa; como gana la primera regla que
encaja, prevalece sobre la regla original, que no se toca. Lo que el ajuste no cambia se copia
de la regla original: fuentes de altura, material, visibilidad… Quitar todos los ajustes de una
capa borra su entrada y la capa vuelve a su regla original.

El resto del archivo (comentarios, orden, reglas) se conserva tal cual. Si el YAML no permite
insertar el bloque de forma segura (p. ej. `layers` en línea), se reescribe entero y se avisa.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

import yaml

from extrude.config import EXTRUDED_KINDS, Config, ConfigError, Kind, LayerRule, parse_config
from extrude.errors import ExtrudeError

BLOCK_START = "# >>> extrude-visor: ajustes hechos en el visor (bloque gestionado)"
BLOCK_PREFIX = "# >>> extrude-visor"
BLOCK_END = "# <<< extrude-visor"
LAYER_MARK = "# capa: "
ADJUST_MARK = "# ajustes: "
MAX_HEIGHT_M = 1000.0
# Campos de la regla original que deciden la altura: una altura fija los sustituye
HEIGHT_FIELDS = (
    "height_from",
    "attribute",
    "text_regex",
    "text_layers",
    "text_tolerance",
    "value_means",
    "floor_offset",
    "floors",
    "block_values",
    "default_height",
    "height",
)
# Campos que deciden la base: el apoyo automático los sustituye
BASE_FIELDS = ("base_from_elevation", "base_from_terrain", "base_auto")
# Valores de partida al clasificar una capa desde el visor (los mismos del borrador)
KIND_DEFAULTS: dict[str, dict[str, Any]] = {
    Kind.BUILDING.value: {"floor_height": 3.0, "default_height": 9.0},
    Kind.ROAD.value: {"height": 0.05},
    Kind.WATER.value: {"height": 0.02},
    Kind.VEGETATION.value: {"height": 0.1},
}
EXTRUDED_VALUES = frozenset(k.value for k in EXTRUDED_KINDS)
KIND_VALUES = frozenset(k.value for k in Kind)
LAYERS_LINE = re.compile(r"^layers:[ \t]*(#.*)?$")
ADJUST_KEYS = ("height", "support", "kind", "z_height")
Adjust = dict[str, Any]
Entry = tuple[str, Adjust, dict[str, Any]]  # capa, ajustes, regla resultante


class HeightError(ExtrudeError):
    """El ajuste pedido no se puede aplicar a esa capa."""


@dataclass(frozen=True)
class LayerHeight:
    """Lo que el panel de capas muestra de una capa."""

    name: str
    kind: str | None
    editable: bool
    reason: str | None  # por qué no es editable
    source: str | None  # de dónde sale hoy la altura: viewer | fixed | text | attribute | …
    fixed: float | None  # altura fijada desde el visor (bloque gestionado)
    rule_height: float | None  # altura fija de la regla original, si la tiene
    base_mode: str | None = None  # auto | terrain | elevation | fixed
    support_adjusted: bool = False  # el apoyo automático se activó desde el visor
    kind_adjusted: bool = False  # la capa se clasificó desde el visor

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "editable": self.editable,
            "reason": self.reason,
            "source": self.source,
            "fixed": self.fixed,
            "ruleHeight": self.rule_height,
            "baseMode": self.base_mode,
            "supportAdjusted": self.support_adjusted,
            "kindAdjusted": self.kind_adjusted,
        }


def exact_pattern(layer: str) -> str:
    """Patrón glob que encaja solo con `layer` (escapa `*`, `?` y `[`)."""
    return re.sub(r"([*?\[])", r"[\1]", layer)


def base_mode(rule: LayerRule) -> str:
    if rule.base_auto:
        return "auto"
    if rule.base_from_terrain:
        return "terrain"
    if rule.base_from_elevation:
        return "elevation"
    return "fixed"


def _block_bounds(lines: list[str]) -> tuple[int, int] | None:
    start = next((i for i, line in enumerate(lines) if line.strip().startswith(BLOCK_PREFIX)), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip() == BLOCK_END), None)
    if end is None:
        raise HeightError(
            "El bloque de ajustes del visor está incompleto en el YAML.",
            f"Borra desde «{BLOCK_PREFIX}…» hasta el final de sus reglas y vuelve a aplicar.",
        )
    return start, end


def _strip_block(text: str) -> str:
    """YAML sin el bloque gestionado."""
    lines = text.splitlines(keepends=True)
    bounds = _block_bounds(lines)
    if bounds is None:
        return text
    return "".join(lines[: bounds[0]] + lines[bounds[1] + 1 :])


def current_adjustments(text: str) -> dict[str, Adjust]:
    """Ajustes del visor que contiene el YAML: {capa: {height?, support?, kind?}}."""
    lines = text.splitlines()
    bounds = _block_bounds(lines)
    if bounds is None:
        return {}
    body = lines[bounds[0] + 1 : bounds[1]]
    names: list[str] = []
    declared: dict[str, Adjust] = {}
    for line in body:
        stripped = line.strip()
        if stripped.startswith(LAYER_MARK):
            names.append(stripped[len(LAYER_MARK) :])
        elif stripped.startswith(ADJUST_MARK) and names:
            try:
                data = json.loads(stripped[len(ADJUST_MARK) :])
            except ValueError:
                continue
            if isinstance(data, dict):
                declared[names[-1]] = {k: data[k] for k in ADJUST_KEYS if data.get(k) is not None}
    try:
        rules = yaml.safe_load("\n".join(body)) or []
    except yaml.YAMLError:
        return {}
    if not isinstance(rules, list) or len(rules) != len(names):
        return {}
    out: dict[str, Adjust] = {}
    for name, rule in zip(names, rules, strict=True):
        if name in declared:
            out[name] = declared[name]
        elif isinstance(rule, dict) and isinstance(rule.get("height"), int | float):
            out[name] = {"height": float(rule["height"])}  # bloques de versiones anteriores
    return out


def current_heights(text: str) -> dict[str, float]:
    """Alturas fijadas desde el visor que contiene el YAML."""
    return {
        name: float(adjust["height"])
        for name, adjust in current_adjustments(text).items()
        if adjust.get("height") is not None
    }


def _source(raw_rule: dict[str, Any]) -> str:
    if raw_rule.get("height_from"):
        return str(raw_rule["height_from"])
    if raw_rule.get("floors") is not None:
        return "floors"
    if raw_rule.get("height") is not None:
        return "fixed"
    if raw_rule.get("default_height") is not None:
        return "default"
    return "none"


def _effective_source(raw_rule: dict[str, Any], adjust: Adjust) -> str:
    """Fuente de altura vigente: con `z_height: ignore` la cota Z ya no cuenta."""
    source = _source(raw_rule)
    if source == "elevation" and adjust.get("z_height") == "ignore":
        rest = {k: v for k, v in raw_rule.items() if k != "height_from"}
        return _source(rest)
    return source


def _base(text: str) -> tuple[str, dict[str, Adjust], dict[str, Any], Config]:
    base_text, adjustments = _strip_block(text), current_adjustments(text)
    try:
        raw = yaml.safe_load(base_text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"El YAML del proyecto no es válido: {exc}", "Corrígelo.") from exc
    if not isinstance(raw, dict):
        raise ConfigError("El YAML del proyecto no es un mapa.", "Revisa el archivo.")
    return base_text, adjustments, raw, parse_config(raw, source="config.yaml")


def describe(text: str, layers: list[str]) -> list[LayerHeight]:
    """Estado de cada capa según el YAML asociado al proyecto (con los ajustes aplicados)."""
    _, adjustments, raw, base_config = _base(text)
    raw_rules = raw.get("layers") or []
    final = parse_config(yaml.safe_load(text) or {}, source="config.yaml")
    out: list[LayerHeight] = []
    for name in layers:
        rule = final.rule_for(name)
        adjust = adjustments.get(name, {})
        if rule is None:
            why = "Ninguna regla la convierte."
            out.append(LayerHeight(name, None, False, why, None, None, None))
            continue
        if rule.kind not in EXTRUDED_KINDS:
            why = f"Las capas de tipo «{rule.kind.value}» no se extruyen: no tienen altura."
            out.append(
                LayerHeight(
                    name,
                    rule.kind.value,
                    False,
                    why,
                    None,
                    None,
                    None,
                    kind_adjusted="kind" in adjust,
                )
            )
            continue
        fixed = adjust.get("height")
        original = base_config.rule_for(name)
        raw_rule = raw_rules[base_config.layers.index(original)] if original is not None else {}
        out.append(
            LayerHeight(
                name,
                rule.kind.value,
                True,
                None,
                "viewer" if fixed is not None else _effective_source(raw_rule, adjust),
                float(fixed) if fixed is not None else None,
                original.height if original is not None else None,
                base_mode(rule),
                adjust.get("support") == "auto",
                "kind" in adjust,
            )
        )
    return out


def _override(raw_rule: dict[str, Any] | None, layer: str, adjust: Adjust) -> dict[str, Any]:
    """Regla exacta de la capa: la original con los ajustes del visor encima."""
    rule: dict[str, Any] = {"match": exact_pattern(layer)}
    kind = adjust.get("kind")
    if kind is not None:
        rule["kind"] = kind
        if kind == Kind.IGNORE.value:
            rule["reviewed"] = True  # decisión tomada: deja de proponerse como pendiente
            return rule
        rule.update(KIND_DEFAULTS.get(kind, {}))
        if kind in EXTRUDED_VALUES:
            rule["base_auto"] = True
    elif raw_rule is not None:
        rule.update({k: v for k, v in raw_rule.items() if k != "match"})
    if adjust.get("support") == "auto":
        for key in BASE_FIELDS:
            rule.pop(key, None)
        rule["base_auto"] = True
    if adjust.get("z_height") == "ignore" and rule.get("height_from") == "elevation":
        del rule["height_from"]  # queda default_height: provisional, y así se informa
    if adjust.get("height") is not None:
        for key in HEIGHT_FIELDS:
            rule.pop(key, None)
        rule["height"] = adjust["height"]
    return rule


def _block(rules: list[Entry], indent: str) -> str:
    lines = [f"{indent}{BLOCK_START}\n"]
    for layer, adjust, rule in rules:
        dumped = yaml.safe_dump([rule], sort_keys=False, allow_unicode=True, width=1000)
        lines.append(f"{indent}{LAYER_MARK}{layer}\n")
        lines.append(f"{indent}{ADJUST_MARK}{json.dumps(adjust, ensure_ascii=False)}\n")
        lines.extend(f"{indent}{line}\n" for line in dumped.splitlines())
    lines.append(f"{indent}{BLOCK_END}\n")
    return "".join(lines)


def validate_height(layer: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise HeightError(f"La altura de «{layer}» no es un número.", "Escribe metros, p. ej. 12.5")
    height = float(value)
    if not math.isfinite(height) or height <= 0 or height > MAX_HEIGHT_M:
        raise HeightError(
            f"La altura de «{layer}» debe estar entre 0 y {MAX_HEIGHT_M:g} m.",
            "Escribe una altura en metros mayor que 0.",
        )
    return round(height, 3)


def _validate(layer: str, change: Adjust) -> Adjust:
    clean: Adjust = {}
    for key, value in change.items():
        if key not in ADJUST_KEYS:
            raise HeightError(f"Ajuste desconocido para «{layer}»: {key}.", "Recarga el visor.")
        if value is None:
            clean[key] = None
        elif key == "height":
            clean[key] = validate_height(layer, value)
        elif key == "support":
            if value != "auto":
                raise HeightError(f"Apoyo no válido para «{layer}».", "Usa «auto» o quítalo.")
            clean[key] = "auto"
        elif key == "z_height":
            if value != "ignore":
                raise HeightError(f"Ajuste no válido para «{layer}».", "Recarga el visor.")
            clean[key] = "ignore"
        else:
            if value not in KIND_VALUES:
                raise HeightError(
                    f"Tipo de capa no válido para «{layer}»: {value}.",
                    "Usa building, road, water, vegetation, terrain, line o ignore.",
                )
            clean[key] = value
    return clean


def apply_adjustments(text: str, changes: dict[str, Adjust]) -> str:
    """YAML con los ajustes pedidos: {capa: {height?, support?, kind?}}; un valor None quita
    ese ajuste. Los ajustes anteriores de la capa que no se mencionan se conservan."""
    base_text, adjustments, raw, config = _base(text)
    raw_rules = raw.get("layers") or []
    for layer, change in changes.items():
        if not isinstance(change, dict):
            raise HeightError(f"Ajuste no válido para «{layer}».", "Recarga el visor.")
        merged = {**adjustments.get(layer, {}), **_validate(layer, change)}
        merged = {k: v for k, v in merged.items() if v is not None}
        if merged:
            adjustments[layer] = merged
        else:
            adjustments.pop(layer, None)
    rules: list[Entry] = []
    for layer in sorted(adjustments):
        adjust = adjustments[layer]
        original = config.rule_for(layer)
        kind = adjust.get("kind") or (original.kind.value if original is not None else None)
        needs_extrusion = any(adjust.get(k) is not None for k in ("height", "support", "z_height"))
        if kind is None or (needs_extrusion and kind not in EXTRUDED_VALUES):
            if layer in changes:
                raise HeightError(
                    f"La capa «{layer}» no se extruye con la configuración actual.",
                    "Solo se ajusta altura o apoyo de edificios, viales, agua y vegetación.",
                )
            continue  # ajuste antiguo cuya capa ya no existe en el YAML: se descarta
        raw_rule = raw_rules[config.layers.index(original)] if original is not None else None
        rules.append((layer, adjust, _override(raw_rule, layer, adjust)))

    result = _insert(base_text, rules) if rules else base_text
    if result is None:
        merged_raw = dict(raw)
        merged_raw["layers"] = [rule for _, _, rule in rules] + list(raw_rules)
        result = (
            "# Reescrito por Extrude al aplicar ajustes desde el visor (sin los comentarios).\n"
            + yaml.safe_dump(merged_raw, sort_keys=False, allow_unicode=True, width=1000)
        )
    final = parse_config(yaml.safe_load(result) or {}, source="config.yaml")
    for layer, adjust, _ in rules:
        matched = final.rule_for(layer)
        ok = matched is not None and (
            (adjust.get("height") is None or matched.height == adjust["height"])
            and (adjust.get("support") != "auto" or matched.base_auto)
            and (adjust.get("kind") is None or matched.kind.value == adjust["kind"])
        )
        if not ok:
            raise HeightError(
                f"No se pudo aplicar el ajuste de «{layer}» en el YAML.",
                "Descarga la configuración, edítala a mano y vuelve a convertir.",
            )
    return result


def apply_heights(text: str, changes: dict[str, float | None]) -> str:
    """YAML con las alturas pedidas: {capa: metros} fija; {capa: None} quita la fijada."""
    return apply_adjustments(text, {layer: {"height": value} for layer, value in changes.items()})


def _insert(base_text: str, rules: list[Entry]) -> str | None:
    """Inserta el bloque tras la línea `layers:`; None si el YAML no lo permite sin riesgo."""
    lines = base_text.splitlines(keepends=True)
    index = next(
        (i for i, line in enumerate(lines) if LAYERS_LINE.match(line.rstrip("\r\n"))), None
    )
    if index is None:
        return None
    indent = ""
    for line in lines[index + 1 :]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") or stripped == "-":
            indent = line[: len(line) - len(line.lstrip())]
            break
        return None  # `layers:` sin lista debajo
    else:
        return None
    if not lines[index].endswith("\n"):
        lines[index] += "\n"
    return "".join(lines[: index + 1]) + _block(rules, indent) + "".join(lines[index + 1 :])
