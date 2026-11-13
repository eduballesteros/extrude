"""Plan de materialidad confirmado (`materials.json`): esquema estricto y validación.

Contrato de §9 de docs/REQUISITOS_MATERIALIDAD_IA.md, cerrado para la Fase 0:

- El plan es independiente del prompt: la descripción original es opcional (se puede borrar,
  RS-10) y nunca se reinterpreta para reconstruir las asignaciones.
- Solo contiene asignaciones resueltas a IDs concretos de `objects.json`. Las referencias
  pendientes y los conflictos de un borrador de IA no caben aquí.
- Una omisión voluntaria se registra en `omissions`; no cuenta como parte cumplida.
- Validación de esquema (pydantic, `extra=forbid`, sin NaN/infinito) y validación semántica
  contra el catálogo de objetos y la biblioteca: revisión geométrica, IDs, superficies presentes
  en cada objeto, material y versión existentes, y ningún par objeto + superficie asignado dos
  veces (dos instrucciones incompatibles no se resuelven por orden).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from pydantic.alias_generators import to_camel

from extrude.errors import ExtrudeError
from extrude.material_library import DEFAULT_LIBRARY, MaterialLibrary

PLAN_NAME = "materials.json"
PLAN_SCHEMA_VERSION = 1
MAX_PLAN_BYTES = 5 * 1024 * 1024
MAX_TILE_SIZE_M = 100.0

ObjectId = Annotated[str, StringConstraints(pattern=r"^obj-[0-9a-f]{16}(-[0-9]+)?$")]
SurfaceKey = Literal[
    "generic",
    "facade_exterior",
    "facade_courtyard",
    "roof",
    "foundation",
    "ground",
    "underside",
]  # = surfaces.SURFACE_KEYS (lo comprueba test_materiality)


class Origin(StrEnum):
    MANUAL = "manual"  # edición manual confirmada: prioridad sobre interpretaciones nuevas
    AI_REVIEWED = "ai_reviewed"  # propuesta de IA revisada y confirmada por el usuario


class _Strict(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        alias_generator=to_camel,
        populate_by_name=True,
    )


TileSize = Annotated[float, Field(gt=0, le=MAX_TILE_SIZE_M)]


class Parameters(_Strict):
    """Ajustes permitidos sobre el preset (RF-12). None = valor del preset."""

    tile_size_m: tuple[TileSize, TileSize] | None = None
    rotation_deg: float | None = Field(default=None, ge=-360, le=360)
    roughness: float | None = Field(default=None, ge=0, le=1)
    color: Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")] | None = None


class Assignment(_Strict):
    object_ids: list[ObjectId] = Field(min_length=1)
    surface: SurfaceKey
    material_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,47}$")]
    material_version: Annotated[str, StringConstraints(pattern=r"^[0-9]{1,6}$")]
    parameters: Parameters = Parameters()
    origin: Origin
    reason: Annotated[str, StringConstraints(max_length=500)] | None = None


class Omission(_Strict):
    """Parte de la descripción que el usuario decidió no aplicar (o no realizable)."""

    text: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    reason: Annotated[str, StringConstraints(max_length=500)] | None = None


Note = Annotated[str, StringConstraints(max_length=400)]


class AiTrace(_Strict):
    """Trazabilidad opcional de una propuesta de IA revisada (sin credenciales ni contexto)."""

    provider: Annotated[str, StringConstraints(pattern=r"^[a-z]{1,20}$")]
    model: Annotated[str, StringConstraints(max_length=100)]
    simulated: bool = False
    prompt_version: Annotated[str, StringConstraints(max_length=40)]
    summary: Annotated[str, StringConstraints(max_length=800)]
    explicit_requirements: list[Note] = Field(default_factory=list, max_length=40)
    assumptions: list[Note] = Field(default_factory=list, max_length=40)
    created_at: Annotated[str, StringConstraints(max_length=40)] | None = None


class MaterialPlan(_Strict):
    schema_version: Literal[1]
    project_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,64}$")]
    geometry_revision: Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
    material_revision: Annotated[str, StringConstraints(pattern=r"^mat-[0-9]{3,}$")]
    catalog_version: Annotated[str, StringConstraints(pattern=r"^[a-z0-9_.-]{1,32}$")]
    assignments: list[Assignment] = Field(default_factory=list)
    omissions: list[Omission] = Field(default_factory=list)
    description: Annotated[str, StringConstraints(max_length=10_000)] | None = None
    ai: AiTrace | None = None  # se puede borrar `description` y conservar esto (RS-10)

    def to_json(self) -> str:
        data = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        return json.dumps(data, indent=2, ensure_ascii=False)


@dataclass(frozen=True)
class PlanIssue:
    code: str  # revision | catalog_version | unknown_object | missing_surface | …
    message: str
    assignment: int | None = None  # índice en `assignments`


def _reject_constant(name: str) -> None:
    raise ValueError(f"Valor no finito no permitido: {name}")


def parse_plan(text: str) -> MaterialPlan:
    """JSON → plan validado por esquema. NaN/Infinity y claves desconocidas son errores."""
    try:
        data = json.loads(text, parse_constant=_reject_constant)
        return MaterialPlan.model_validate(data)
    except (ValueError, ValidationError) as exc:
        raise ExtrudeError(
            f"materials.json no cumple el esquema: {exc}",
            "Corrige el archivo o vuelve a generarlo desde Extrude.",
        ) from exc


def load_plan(path: Path) -> MaterialPlan:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ExtrudeError(f"No se puede leer '{path}': {exc}.", "Revisa la ruta.") from exc
    if size > MAX_PLAN_BYTES:
        raise ExtrudeError(
            f"'{path.name}' ocupa {size:,} bytes (máximo {MAX_PLAN_BYTES:,}).",
            "Un plan de materialidad no necesita tanto: revisa que sea el archivo correcto.",
        )
    return parse_plan(path.read_text(encoding="utf-8"))


def validate_plan(
    plan: MaterialPlan,
    catalog: Mapping[str, Any],
    library: MaterialLibrary = DEFAULT_LIBRARY,
) -> list[PlanIssue]:
    """Problemas semánticos del plan frente al catálogo y la biblioteca (vacío = válido)."""
    issues: list[PlanIssue] = []
    if plan.geometry_revision != catalog.get("geometryRevision"):
        issues.append(
            PlanIssue(
                "revision",
                f"El plan es de la revisión geométrica {plan.geometry_revision} y el modelo "
                f"es {catalog.get('geometryRevision')}: revisa las correspondencias antes de "
                "aplicarlo.",
            )
        )
    if plan.catalog_version != library.version:
        issues.append(
            PlanIssue(
                "catalog_version",
                f"Biblioteca {plan.catalog_version} no disponible (actual: {library.version}).",
            )
        )
    surfaces = {o["id"]: set(o.get("surfaces", {})) for o in catalog.get("objects", [])}
    labels = {o["id"]: o.get("label") or o["id"] for o in catalog.get("objects", [])}
    seen: dict[tuple[str, str], int] = {}
    for index, item in enumerate(plan.assignments):
        if library.get(item.material_id, item.material_version) is None:
            known = "otra versión" if library.has_id(item.material_id) else "no existe"
            issues.append(
                PlanIssue(
                    "unknown_material",
                    f"Material {item.material_id} v{item.material_version}: {known} en "
                    f"{library.version}.",
                    index,
                )
            )
        if len(set(item.object_ids)) != len(item.object_ids):
            issues.append(PlanIssue("duplicate_object", "IDs repetidos en la asignación.", index))
        for object_id in dict.fromkeys(item.object_ids):
            if object_id not in surfaces:
                issues.append(
                    PlanIssue("unknown_object", f"{object_id} no está en el catálogo.", index)
                )
                continue
            if item.surface not in surfaces[object_id]:
                available = ", ".join(sorted(surfaces[object_id])) or "ninguna"
                issues.append(
                    PlanIssue(
                        "missing_surface",
                        f"{labels[object_id]} no tiene superficie {item.surface} "
                        f"(disponibles: {available}).",
                        index,
                    )
                )
            key = (object_id, item.surface)
            if key in seen:
                issues.append(
                    PlanIssue(
                        "conflict",
                        f"{labels[object_id]} / {item.surface} ya se asigna en la asignación "
                        f"{seen[key] + 1}: un plan confirmado no puede tener dos materiales "
                        "para la misma superficie.",
                        index,
                    )
                )
            else:
                seen[key] = index
    return issues


def effective_assignments(plan: MaterialPlan) -> dict[tuple[str, str], Assignment]:
    """(ID de objeto, superficie) → asignación. Requiere un plan sin conflictos; las superficies
    sin entrada conservan el material de su capa."""
    table: dict[tuple[str, str], Assignment] = {}
    for item in plan.assignments:
        for object_id in item.object_ids:
            key = (object_id, item.surface)
            if key in table:
                raise ValueError(f"Conflicto sin resolver en {key}: valida el plan antes")
            table[key] = item
    return table
