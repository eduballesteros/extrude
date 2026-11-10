"""Configuración por proyecto (YAML) validada con pydantic.

Cada proyecto define sus reglas por capa: qué es cada capa (`kind`) y de dónde sale la altura.
Prioridad de la altura de un polígono:
  1. `height_from` (attribute | text | elevation), si encuentra un valor;
  2. `floors` × `floor_height`;
  3. `height` (altura fija);
  4. `default_height`.
Si ninguna da valor: los edificios se descartan (y se reportan); viales, agua y vegetación
quedan como superficie plana.
"""

from __future__ import annotations

import fnmatch
import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from extrude.errors import ExtrudeError
from extrude.model import LengthUnit

DEFAULT_FLOOR_HEIGHT_M = 3.0
DEFAULT_TOLERANCE_M = 0.01
DEFAULT_INSTANCING_MIN = 10
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class ConfigError(ExtrudeError):
    """El YAML de configuración no es válido."""


class Kind(StrEnum):
    BUILDING = "building"
    TERRAIN = "terrain"
    ROAD = "road"
    WATER = "water"
    VEGETATION = "vegetation"
    LINE = "line"
    IGNORE = "ignore"


EXTRUDED_KINDS = frozenset({Kind.BUILDING, Kind.ROAD, Kind.WATER, Kind.VEGETATION})


class HeightSource(StrEnum):
    ATTRIBUTE = "attribute"
    TEXT = "text"
    ELEVATION = "elevation"


class ValueMeaning(StrEnum):
    FLOORS = "floors"
    METERS = "meters"


Unit01 = Annotated[float, Field(ge=0.0, le=1.0)]
Positive = Annotated[float, Field(gt=0.0)]
NonNegative = Annotated[float, Field(ge=0.0)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Material(_Strict):
    color: str = "#cccccc"
    roughness: Unit01 = 0.9
    metalness: Unit01 = 0.0
    opacity: Unit01 = 1.0

    @field_validator("color")
    @classmethod
    def _hex(cls, value: str) -> str:
        if not HEX_COLOR.match(value):
            raise ValueError(f"color '{value}' no es un hexadecimal #rrggbb")
        return value.lower()


DEFAULT_MATERIALS: dict[Kind, Material] = {
    Kind.BUILDING: Material(color="#d9d4c7", roughness=0.9),
    Kind.TERRAIN: Material(color="#a8a48f", roughness=1.0),
    Kind.ROAD: Material(color="#555555", roughness=1.0),
    Kind.WATER: Material(color="#4a7fa8", roughness=0.2),
    Kind.VEGETATION: Material(color="#6b8f4e", roughness=1.0),
    Kind.LINE: Material(color="#222222", roughness=1.0),
    Kind.IGNORE: Material(),
}


class LayerRule(_Strict):
    match: str = Field(description="Patrón glob de capa, sin distinguir mayúsculas")
    kind: Kind
    height_from: HeightSource | None = None
    attribute: str | None = None
    text_regex: str = r"(\d+)\s*P"
    text_layers: list[str] | None = None
    value_means: ValueMeaning = ValueMeaning.FLOORS
    floor_offset: int = 0
    height: NonNegative | None = None
    floors: Annotated[int, Field(ge=0)] | None = None
    floor_height: Positive = DEFAULT_FLOOR_HEIGHT_M
    default_height: NonNegative | None = None
    base_elevation: float = 0.0
    detect_holes: bool = True
    bottom_cap: bool = False
    material: Material | None = None
    visible: bool = True

    @field_validator("text_regex")
    @classmethod
    def _regex(cls, value: str) -> str:
        try:
            compiled = re.compile(value)
        except re.error as exc:
            raise ValueError(f"text_regex no es una expresión regular válida: {exc}") from exc
        if compiled.groups < 1:
            raise ValueError("text_regex debe tener un grupo de captura con el número")
        return value

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if self.height_from is HeightSource.ATTRIBUTE and not self.attribute:
            raise ValueError("height_from: attribute requiere 'attribute' (etiqueta del ATTRIB)")
        return self

    def matches(self, layer: str) -> bool:
        return fnmatch.fnmatchcase(layer.upper(), self.match.upper())

    def text_layer_matches(self, layer: str) -> bool:
        if self.text_layers is None:
            return True
        return any(fnmatch.fnmatchcase(layer.upper(), p.upper()) for p in self.text_layers)

    @property
    def effective_material(self) -> Material:
        return self.material or DEFAULT_MATERIALS[self.kind]


class Config(_Strict):
    units: Literal["auto"] | LengthUnit = "auto"
    recenter: bool = True
    tolerance: Positive = DEFAULT_TOLERANCE_M
    crs: str | None = None  # p. ej. "EPSG:25830"; solo se copia a scene.extras
    instancing_min: Annotated[int, Field(ge=0)] = DEFAULT_INSTANCING_MIN  # 0 = desactivado
    layers: list[LayerRule] = Field(default_factory=list)

    def rule_for(self, layer: str) -> LayerRule | None:
        """Primera regla cuyo patrón encaja (el orden del YAML importa)."""
        return next((r for r in self.layers if r.matches(layer)), None)

    @property
    def units_override(self) -> LengthUnit | None:
        return None if self.units == "auto" else LengthUnit(self.units)


def load_config(path: Path) -> Config:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"No se puede leer '{path}': {exc}.", "Comprueba la ruta.") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"'{path.name}' no es YAML válido: {exc}",
            "Revisa la indentación y las comillas (los regex van entre comillas simples).",
        ) from exc
    return parse_config(raw or {}, source=path.name)


def parse_config(raw: object, *, source: str = "config") -> Config:
    try:
        return Config.model_validate(raw)
    except ValidationError as exc:
        problems = "\n".join(
            f"    - {'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigError(
            f"Configuración inválida en '{source}':\n{problems}",
            "Corrige esos campos; todas las opciones están en docs/CONFIG.md.",
        ) from exc
