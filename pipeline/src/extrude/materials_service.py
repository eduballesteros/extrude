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

import json
import re
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from extrude.errors import ExtrudeError
from extrude.material_library import DEFAULT_LIBRARY, MaterialLibrary
from extrude.materiality import MaterialPlan, parse_plan, validate_plan
from extrude.materialize import GlbData, check_base, glb_catalog, materialize
from extrude.textures import PATTERNS, texture

BASE_GLB = "model.glb"
PREVIEW_GLB = "model.preview.glb"
MATERIALIZED_GLB = "model.materialized.glb"
PLAN_FILE = "materials.json"
HISTORY_DIR = "materials"
REVISION = re.compile(r"^mat-(\d{3,})\.json$")
MAX_PLAN_BYTES = 5 * 1024 * 1024

_lock = threading.Lock()  # una materialización a la vez por proceso (CPU y archivos)


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
                "tileSizeM": list(p.tile_size_m),
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


def _write_atomic(path: Path, data: bytes) -> None:
    partial = path.with_name(f"{path.name}.partial")
    partial.write_bytes(data)
    partial.replace(path)


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


def apply_plan(out_dir: Path, body: bytes, *, preview: bool) -> dict[str, Any]:
    """Valida y aplica el plan al `model.glb` del trabajo. Devuelve qué archivos se escribieron."""
    base_path = out_dir / BASE_GLB
    if not base_path.is_file():
        raise ExtrudeError(
            "Este trabajo no tiene un GLB base único.",
            "Los modelos teselados no se materializan en esta versión: convierte sin teselas.",
        )
    plan = _parse(body)
    with _lock:
        base = base_path.read_bytes()
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
        name = PREVIEW_GLB if preview else MATERIALIZED_GLB
        if preview:
            _write_atomic(out_dir / name, data)
        else:
            text = plan.to_json().encode("utf-8")
            # Primero la copia inmutable; después GLB y plan vigentes. Si algo falla antes del
            # último replace, el estado confirmado anterior sigue intacto y coherente.
            (history / f"{plan.material_revision}.json").write_bytes(text)
            _write_atomic(out_dir / name, data)
            _write_atomic(out_dir / PLAN_FILE, text)
    return {
        "preview": preview,
        "materialRevision": plan.material_revision,
        "geometryRevision": plan.geometry_revision,
        "file": name,
        "plan": None if preview else PLAN_FILE,
        "stats": asdict(stats),
    }


def confirmed_plan(out_dir: Path) -> dict[str, Any] | None:
    path = out_dir / PLAN_FILE
    if not path.is_file():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data
