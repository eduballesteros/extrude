"""Estado persistente de un proyecto (trabajo de `extrude serve`): revisiones confirmadas.

Cada confirmación (materiales manuales, cambios con IA o solo vistas) crea una revisión
`rev-NNN` con su GLB base (geometría vigente) y su plan de materiales. Deshacer y rehacer mueven
el puntero y restauran los archivos servidos; nunca se borra una revisión, así que un resultado
anterior siempre es recuperable. Archivos en `out/`:

- `project.json`: historial y revisión actual (escritura atómica).
- `model.glb`: GLB base de la conversión (inmutable, rev-000). Si está comprimido o el proyecto
  es teselado, rev-000 es `model.master.glb` (maestro sin comprimir, ver master.py).
- `revisions/rev-NNN.glb`: geometría editada de cada revisión que cambia la geometría.
- `materials/mat-NNN.json`: planes confirmados (inmutables).
- `model.current.glb`, `materials.json`, `model.materialized.glb`: estado actual servido.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from extrude.errors import ExtrudeError
from extrude.master import editable_base, regenerate_tiles

STATE_FILE = "project.json"
BASE_GLB = "model.glb"
CURRENT_GLB = "model.current.glb"
PLAN_FILE = "materials.json"
MATERIALIZED_GLB = "model.materialized.glb"
REVISIONS_DIR = "revisions"
HISTORY_DIR = "materials"

LOCK = threading.RLock()  # una escritura de proyecto a la vez por proceso


@dataclass
class Revision:
    id: str
    parent: str | None
    kind: str  # base | materials | edit | viewer
    created_at: str
    summary: str
    base: str  # ruta relativa a out/ del GLB base de esta revisión
    geometry_revision: str
    plan: str | None = None  # materials/mat-NNN.json
    material_revision: str | None = None
    plan_id: str | None = None
    operations: list[dict[str, Any]] = field(default_factory=list)
    # Estado del visor de esta revisión: vistas creadas (con su estado) y sol antes/después
    viewer: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent": self.parent,
            "kind": self.kind,
            "createdAt": self.created_at,
            "summary": self.summary,
            "geometryRevision": self.geometry_revision,
            "materialRevision": self.material_revision,
            "planId": self.plan_id,
            "operations": self.operations,
            "viewer": self.viewer,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def write_atomic(path: Path, data: bytes) -> None:
    partial = path.with_name(f"{path.name}.partial")
    partial.write_bytes(data)
    partial.replace(path)


class ProjectState:
    def __init__(self, out_dir: Path) -> None:
        self.out = out_dir
        self.revisions: list[Revision] = []
        self.current = "rev-000"
        self.redo: list[str] = []

    # Carga --------------------------------------------------------------------------------------

    @classmethod
    def load(cls, out_dir: Path, geometry_revision: str | None = None) -> ProjectState:
        state = cls(out_dir)
        path = out_dir / STATE_FILE
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            state.revisions = [Revision(**r) for r in data["revisions"]]
            state.current = data["current"]
            state.redo = list(data.get("redo", []))
            return state
        if geometry_revision is None:
            raise ExtrudeError("Proyecto sin estado.", "Vuelve a convertir el plano.")
        # Proyecto anterior a project.json: rev-000 con el plan confirmado que hubiera
        plan, material = None, None
        if (out_dir / PLAN_FILE).is_file():
            data = json.loads((out_dir / PLAN_FILE).read_text(encoding="utf-8"))
            material = data.get("materialRevision")
            candidate = out_dir / HISTORY_DIR / f"{material}.json"
            plan = f"{HISTORY_DIR}/{material}.json" if candidate.is_file() else None
        state.revisions = [
            Revision(
                "rev-000",
                None,
                "base",
                _now(),
                "Modelo convertido",
                editable_base(out_dir).name,
                geometry_revision,
                plan,
                material if plan else None,
            )
        ]
        return state

    def save(self) -> None:
        data = {
            "schemaVersion": 1,
            "current": self.current,
            "redo": self.redo,
            "revisions": [asdict(r) for r in self.revisions],
        }
        text = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
        write_atomic(self.out / STATE_FILE, text)

    # Consulta -----------------------------------------------------------------------------------

    def get(self, revision_id: str) -> Revision:
        for revision in self.revisions:
            if revision.id == revision_id:
                return revision
        raise ExtrudeError(f"Revisión {revision_id} desconocida.", "Recarga el proyecto.")

    @property
    def head(self) -> Revision:
        return self.get(self.current)

    def base_path(self, revision: Revision | None = None) -> Path:
        return self.out / (revision or self.head).base

    def plan_text(self, revision: Revision | None = None) -> str | None:
        revision = revision or self.head
        if revision.plan is None:
            return None
        return (self.out / revision.plan).read_text(encoding="utf-8")

    @property
    def can_undo(self) -> bool:
        return self.head.parent is not None

    @property
    def can_redo(self) -> bool:
        return bool(self.redo)

    def next_id(self) -> str:
        numbers = [int(r.id.split("-")[1]) for r in self.revisions]
        return f"rev-{max(numbers) + 1:03d}"

    def public(self, job_id: str) -> dict[str, Any]:
        head = self.head
        # Antes de la primera confirmación aún no hay model.current.glb: se anuncia el que existe
        current = CURRENT_GLB if (self.out / CURRENT_GLB).is_file() else head.base
        files = {"base": f"/api/jobs/{job_id}/{current}"}
        if head.plan:
            files["materialized"] = f"/api/jobs/{job_id}/{MATERIALIZED_GLB}"
            files["plan"] = f"/api/jobs/{job_id}/{PLAN_FILE}"
        return {
            "revision": head.id,
            "geometryRevision": head.geometry_revision,
            "materialRevision": head.material_revision,
            "canUndo": self.can_undo,
            "canRedo": self.can_redo,
            "modelUrl": files.get("materialized", files["base"]),
            "files": files,
            "history": [r.public() for r in self.revisions],
        }

    # Escritura ----------------------------------------------------------------------------------

    def commit(
        self,
        *,
        kind: str,
        summary: str,
        base_bytes: bytes | None,
        geometry_revision: str,
        plan_text: str | None,
        material_revision: str | None,
        materialized: bytes | None,
        plan_id: str | None = None,
        operations: list[dict[str, Any]] | None = None,
        viewer: dict[str, Any] | None = None,
    ) -> Revision:
        """Nueva revisión hija de la actual. `base_bytes` None = misma geometría."""
        head = self.head
        revision_id = self.next_id()
        base = head.base
        if base_bytes is not None:
            (self.out / REVISIONS_DIR).mkdir(exist_ok=True)
            base = f"{REVISIONS_DIR}/{revision_id}.glb"
            write_atomic(self.out / base, base_bytes)
        plan = (
            head.plan if plan_text is None and material_revision == head.material_revision else None
        )
        if plan_text is not None and material_revision is not None:
            (self.out / HISTORY_DIR).mkdir(exist_ok=True)
            plan = f"{HISTORY_DIR}/{material_revision}.json"
            target = self.out / plan
            if not target.is_file():
                target.write_text(plan_text, encoding="utf-8")
        revision = Revision(
            revision_id,
            head.id,
            kind,
            _now(),
            summary[:300],
            base,
            geometry_revision,
            plan,
            material_revision if plan else None,
            plan_id,
            operations or [],
            viewer or None,
        )
        self.revisions.append(revision)
        previous_current, previous_redo = self.current, self.redo
        self.current = revision_id
        self.redo = []
        try:
            self._publish_transaction(revision, materialized)
        except BaseException:
            self.revisions.pop()
            self.current, self.redo = previous_current, previous_redo
            raise
        return revision

    def _publish(self, revision: Revision, materialized: bytes | None, destination: Path) -> None:
        """Copia al estado servido: base vigente, plan y GLB materializado (o los retira)."""
        source = self.out / revision.base
        if source.name != CURRENT_GLB:
            partial = destination / f"{CURRENT_GLB}.partial"
            shutil.copyfile(source, partial)
            partial.replace(destination / CURRENT_GLB)
        if revision.plan is None:
            for name in (PLAN_FILE, MATERIALIZED_GLB):
                (destination / name).unlink(missing_ok=True)
            return
        if materialized is not None:
            write_atomic(destination / MATERIALIZED_GLB, materialized)
        write_atomic(destination / PLAN_FILE, (self.out / revision.plan).read_bytes())

    def _retile(self, revision: Revision, destination: Path) -> None:
        """Proyecto teselado: teselas y manifest desde el estado vigente (con materiales)."""
        materialized = destination / MATERIALIZED_GLB
        source = (
            materialized if revision.plan and materialized.is_file() else destination / CURRENT_GLB
        )
        if source.is_file():
            regenerate_tiles(destination, source, revision.id)

    def _publish_transaction(self, revision: Revision, materialized: bytes | None) -> None:
        """Prepara toda la entrega antes de publicarla; revierte archivos si falla la publicación.

        Las teselas son inmutables por revisión: un manifest ya descargado continúa siendo válido.
        Los lectores HTTP y de proyectos comparten LOCK con los escritores.
        """
        with tempfile.TemporaryDirectory(
            prefix=".publish-", dir=self.out, ignore_cleanup_errors=True
        ) as work:
            stage = Path(work)
            if not stage.resolve().is_relative_to(self.out.resolve()):
                raise ExtrudeError("Directorio de publicación inválido.", "Revisa el proyecto.")
            self._publish(revision, materialized, stage)
            if (self.out / "manifest.json").is_file():
                shutil.copyfile(self.out / "manifest.json", stage / "manifest.json")
            self._retile(revision, stage)
            backup = stage / "backup"
            backup.mkdir()
            installed: list[str] = []
            moved: list[str] = []
            new_tiles: list[Path] = []
            try:
                for tile in (stage / "tiles").glob("*.glb"):
                    destination = self.out / "tiles" / tile.name
                    destination.parent.mkdir(exist_ok=True)
                    if destination.is_file():
                        if destination.read_bytes() != tile.read_bytes():
                            raise ExtrudeError(
                                "La tesela de esta revisión ya existe con otros datos.",
                                "Recarga el proyecto; no se ha sustituido la revisión anterior.",
                            )
                        continue
                    tile.replace(destination)
                    new_tiles.append(destination)
                names = [CURRENT_GLB, MATERIALIZED_GLB, PLAN_FILE, STATE_FILE]
                if (stage / "manifest.json").is_file():
                    names.append("manifest.json")
                for name in names:
                    destination = self.out / name
                    if destination.is_file():
                        destination.replace(backup / name)
                        moved.append(name)
                    prepared = stage / name
                    if prepared.is_file():
                        prepared.replace(destination)
                        installed.append(name)
                installed.append(STATE_FILE)
                self.save()
            except BaseException:
                for name in reversed(installed):
                    target = self.out / name
                    if target.is_file():
                        target.replace(stage / f"failed-{name}")
                for name in reversed(moved):
                    (backup / name).replace(self.out / name)
                for tile in new_tiles:
                    tile.unlink(missing_ok=True)
                (self.out / f"{STATE_FILE}.partial").unlink(missing_ok=True)
                raise

    def move(self, target: str, materialized: bytes | None) -> Revision:
        """Deshacer/rehacer: publica `target` (ya existente) como estado actual."""
        revision = self.get(target)
        previous = self.current
        self.current = target
        try:
            self._publish_transaction(revision, materialized)
        except BaseException:
            self.current = previous
            raise
        return revision

    def undo_target(self) -> Revision:
        if not self.can_undo:
            raise ExtrudeError("No hay nada que deshacer.", "Ya estás en el modelo convertido.")
        parent = self.head.parent
        if parent is None:
            raise ExtrudeError("No hay nada que deshacer.", "Ya estás en el modelo convertido.")
        return self.get(parent)

    def redo_target(self) -> Revision:
        if not self.redo:
            raise ExtrudeError("No hay nada que rehacer.", "Haz un cambio nuevo.")
        return self.get(self.redo[-1])
