"""Materialidad en `extrude serve`: biblioteca, registro de GLB base y aplicación de planes.

Separado del manejador HTTP para probarlo sin red. Reglas (docs/REQUISITOS_MATERIALIDAD_IA.md):

- Previsualizar escribe `model.preview.glb` y no toca el estado confirmado (RF-08).
- Confirmar asigna la siguiente revisión (`mat-001`, `mat-002`…), guarda una copia inmutable en
  `materials/<revisión>.json` y después sustituye `model.materialized.glb` y `materials.json`,
  cada uno con un renombrado atómico (RF-21): nunca queda un archivo a medias, y el GLB lleva su
  `material_revision` para detectar una pareja desfasada. La revisión la pone el servidor.
- Un plan inválido no escribe nada y devuelve los problemas para mostrarlos (RF-07).
"""

from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from extrude.errors import ExtrudeError
from extrude.master import editable_base
from extrude.material_library import DEFAULT_LIBRARY, MaterialLibrary
from extrude.materiality import MaterialPlan, parse_plan, validate_plan
from extrude.materialize import GlbData, check_base, glb_catalog, materialize
from extrude.project_state import LOCK, ProjectState, write_atomic
from extrude.textures import PATTERNS, texture

BASE_GLB = "model.glb"
PREVIEW_GLB = "model.preview.glb"
MATERIALIZED_GLB = "model.materialized.glb"
PLAN_FILE = "materials.json"
HISTORY_DIR = "materials"
REVISION = re.compile(r"^mat-(\d{3,})\.json$")
MAX_PLAN_BYTES = 5 * 1024 * 1024


class PlanRejected(ExtrudeError):  # noqa: N818 (mismo convenio que el resto de errores)
    """El plan no es aplicable; `issues` lleva los problemas uno a uno."""

    def __init__(self, problem: str, fix: str, issues: list[dict[str, Any]]) -> None:
        super().__init__(problem, fix)
        self.issues = issues


def library_payload(library: MaterialLibrary = DEFAULT_LIBRARY) -> dict[str, Any]:
    return {
        "version": library.version,
        "materials": [
            {
                "id": p.id,
                "version": p.version,
                "name": p.name,
                "family": p.family,
                "description": p.description,
                "color": p.color,
                "roughness": p.roughness,
                "metalness": p.metalness,
                "opacity": p.opacity,
                "tileSizeM": list(p.tile_size_m),  # mosaico de textura completo (repetición)
                "piecesPerTile": list(p.pieces) if p.pieces else None,
                "pieceSizeM": [round(v, 4) for v in p.piece_size_m] if p.piece_size_m else None,
                "texture": f"/api/library/{p.pattern}.png" if p.pattern else None,
            }
            for p in library.presets
        ],
    }


def texture_png(name: str) -> bytes | None:
    """PNG de una textura de la biblioteca por nombre de patrón (lista blanca)."""
    return texture(name)[0] if name in PATTERNS else None


def inspect_base(data: bytes) -> str:
    """Comprueba que `data` es un GLB base materializable y devuelve su revisión geométrica."""
    try:
        glb = GlbData(data)
    except (ValueError, KeyError, IndexError) as exc:
        raise ExtrudeError(f"No es un GLB válido ({exc}).", "Usa el model.glb de convert.") from exc
    extras = check_base(glb.doc)
    revision = str(extras["geometry_revision"])
    if not glb_catalog(glb, revision)["objects"]:
        raise ExtrudeError(
            "El GLB no tiene superficies ni UV por objeto.",
            "Vuelve a convertir el DWG/DXF con esta versión de Extrude.",
        )
    return revision


def _next_revision(history: Path) -> str:
    numbers = [int(m.group(1)) for p in history.glob("mat-*.json") if (m := REVISION.match(p.name))]
    return f"mat-{max(numbers, default=0) + 1:03d}"


def _parse(text: bytes) -> MaterialPlan:
    if len(text) > MAX_PLAN_BYTES:
        raise ExtrudeError("El plan es demasiado grande.", "Un plan no necesita más de 5 MB.")
    try:
        decoded = text.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExtrudeError("El plan no está en UTF-8.", "Envía materials.json en UTF-8.") from exc
    return parse_plan(decoded)


def _project(out_dir: Path) -> ProjectState:
    base = GlbData(editable_base(out_dir).read_bytes())
    return ProjectState.load(out_dir, str(check_base(base.doc)["geometry_revision"]))


def apply_plan(
    out_dir: Path, body: bytes, *, preview: bool, expect: str | None = None
) -> dict[str, Any]:
    """Valida y aplica el plan al GLB base VIGENTE del proyecto (con sus ediciones geométricas).

    Confirmar crea una revisión del proyecto (deshacer/rehacer, ver project_state.py)."""
    editable_base(out_dir)  # maestro sin comprimir (comprimidos y teselados incluidos)
    plan = _parse(body)
    with LOCK:
        state = _project(out_dir)
        if expect is not None and not preview and state.current != expect:
            raise StaleRevision(state.current, expect)
        base = state.base_path().read_bytes()
        glb = GlbData(base)
        extras = check_base(glb.doc)
        catalog = glb_catalog(glb, str(extras["geometry_revision"]))
        issues = validate_plan(plan, catalog)
        if issues:
            raise PlanRejected(
                f"El plan tiene {len(issues)} problemas.",
                "Corrígelos en el panel de materiales antes de aplicarlo.",
                [asdict(i) for i in issues],
            )
        if not preview:
            history = out_dir / HISTORY_DIR
            history.mkdir(exist_ok=True)
            plan = plan.model_copy(update={"material_revision": _next_revision(history)})
        data, stats = materialize(base, plan)
        if preview:
            write_atomic(out_dir / PREVIEW_GLB, data)
            name = PREVIEW_GLB
        else:
            state.commit(
                kind="materials",
                summary=f"Materiales {plan.material_revision} "
                f"({len(plan.assignments)} asignaciones)",
                base_bytes=None,
                geometry_revision=plan.geometry_revision,
                plan_text=plan.to_json(),
                material_revision=plan.material_revision,
                materialized=data,
            )
            name = MATERIALIZED_GLB
    return {
        "preview": preview,
        "materialRevision": plan.material_revision,
        "geometryRevision": plan.geometry_revision,
        "file": name,
        "plan": None if preview else PLAN_FILE,
        "stats": asdict(stats),
        "revision": None if preview else state.current,
    }


class StaleRevision(ExtrudeError):  # noqa: N818
    def __init__(self, current: str, expected: str) -> None:
        super().__init__(
            f"El proyecto está en {current}, no en {expected}: no se deshace nada.",
            "Revisa el historial; otro cambio llegó después.",
        )
        self.current = current


def project_status(out_dir: Path, job_id: str) -> dict[str, Any]:
    with LOCK:
        return _project(out_dir).public(job_id)


def step(out_dir: Path, job_id: str, *, forward: bool, expect: str | None = None) -> dict[str, Any]:
    """Deshacer (forward=False) o rehacer: publica otra revisión ya confirmada, sin IA.

    Con `expect`, solo actúa si la revisión actual es esa (compensar una confirmación concreta
    nunca deshace cambios posteriores de otra pestaña o usuario)."""
    with LOCK:
        state = _project(out_dir)
        if expect is not None and state.current != expect:
            raise StaleRevision(state.current, expect)
        if forward:
            target = state.redo_target()
            state.redo.pop()
        else:
            target = state.undo_target()
            state.redo.append(state.current)
        materialized = None
        text = state.plan_text(target)
        if text is not None:
            from extrude.materiality import parse_plan

            materialized, _ = materialize(state.base_path(target).read_bytes(), parse_plan(text))
        state.move(target.id, materialized)
        return state.public(job_id)
