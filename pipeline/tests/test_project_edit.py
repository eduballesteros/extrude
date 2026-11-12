"""Aplicar planes: geometría real, transacción, dependencias, dudas, obsolescencia, deshacer.

Todas las comprobaciones miran el resultado (GLB, revisiones, plan de materiales), no la
implementación: alturas medidas en el GLB, centros recalculados, archivos servidos.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from ai_fixtures import write_model
from ai_helpers import (
    FakeProvider,
    ask,
    copy_job,
    ids_of,
    op,
    plan,
    prepared_viewer,
    refs,
    service,
    targets,
)
from test_ai_planner import without_floor_height

from extrude.ai_planner import AiError, load_project
from extrude.glb_edit import GlbEditor, locate
from extrude.materials_service import apply_plan, step
from extrude.node_tools import tools_dir, validate
from extrude.project_edit import ApplyRequest, apply_edit
from extrude.surfaces import Surface

node_tools = pytest.mark.skipif(
    shutil.which("node") is None or not (tools_dir() / "node_modules" / "gltf-validator").is_dir(),
    reason="Node o tools/node_modules no disponibles (npm install en tools/)",
)


@pytest.fixture(scope="module")
def model_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_model(tmp_path_factory.mktemp("edit") / "out", kiosks=True)


@pytest.fixture
def job(tmp_path: Path, model_dir: Path) -> Path:
    return copy_job(model_dir, tmp_path / "job")


def run(
    job: Path,
    proposal: dict[str, Any],
    ops: list[str] | None = None,
    *,
    preview: bool = False,
    **kw: Any,
) -> dict[str, Any]:
    chosen = ops or [o["id"] for o in proposal["operations"]]
    views = [o for o in proposal["operations"] if o["id"] in chosen and o["tool"] == "view.create"]
    if views and "viewer" not in kw:  # como el visor: efectos preparados antes de confirmar
        kw["viewer"] = prepared_viewer(views)
    request = ApplyRequest(
        planId=proposal["planId"],
        baseRevision=kw.pop("base", proposal["baseRevision"]),
        operations=chosen,
        **kw,
    )
    return apply_edit(job, request, preview=preview)


def facts(job: Path, label: str) -> Any:
    return next(o for o in load_project(job / "out").objects if o.label == label)


def roof_height(job: Path, label: str) -> float:
    """Cota de la cubierta medida en el GLB servido (no en los metadatos)."""
    data = (job / "out" / "model.current.glb").read_bytes()
    editor = GlbEditor(data)
    where = locate(editor.doc)[ids_of(job, label)[0]]
    top = []
    for prim in where.prims:
        p = editor.doc["meshes"][editor.doc["nodes"][where.node]["mesh"]]["primitives"][prim]
        mask = np.rint(editor.read(p["attributes"]["_OBJECT_ID"])).astype(int) == where.index
        surf = np.rint(editor.read(p["attributes"]["_SURFACE"])).astype(int)
        pos = editor.read(p["attributes"]["POSITION"])
        top += pos[mask & (surf == Surface.ROOF), 1].tolist()
        base = pos[mask, 1].min()
    return float(max(top) - base)


def multi(ctx: dict[str, Any]) -> str:
    return plan(
        op(
            "op1",
            "building.set_height",
            {"mode": "delta_floors", "value": 2, "floorHeightM": None},
            targets(refs(ctx, "E-001")),
        ),
        op("op2", "object.move", {"dxM": 0, "dyM": -10}, targets(refs(ctx, "E-007"))),
        op(
            "op3",
            "material.assign",
            {"surface": "facade_exterior", "materialId": "brick_red"},
            targets(kinds=["building"]),
        ),
        op(
            "op4",
            "object.duplicate",
            {"dxM": 40, "dyM": 0, "copies": 1},
            targets(refs(ctx, "E-002")),
        ),
        op(
            "op5",
            "view.create",
            {"name": "Atardecer", "preset": "iso", "time": "sunset", "date": None},
            targets(),
        ),
    )


def test_request_plan_preview_confirm_and_export(job: Path) -> None:
    fake = FakeProvider(multi)
    y7 = facts(job, "E-007").y
    proposal = ask(service(fake), job, "Varios cambios")
    preview = run(job, proposal, preview=True)
    assert preview["url"].endswith("model.preview.glb") and preview["geometryChanged"]
    assert load_project(job / "out").state.current == "rev-000"  # previsualizar no confirma
    result = run(job, proposal)
    assert result["revision"] == "rev-001"
    status = {r["id"]: r["status"] for r in result["results"]}
    assert status == {"op1": "done", "op2": "done", "op3": "done", "op4": "done", "op5": "viewer"}
    assert result["viewerOps"][0]["args"]["time"] == "sunset"
    # Geometría real: cubierta 6 m más alta (2 plantas × 3 m), sin escalar la planta
    assert roof_height(job, "E-001") == pytest.approx(18.0, abs=0.01)
    assert facts(job, "E-001").width == 10.0
    assert facts(job, "E-007").y == pytest.approx(y7 - 10, abs=0.01)
    copy = facts(job, "E-008")
    assert copy.x - facts(job, "E-002").x == pytest.approx(40.0, abs=0.01)
    # Materiales: los nuevos y los heredados por la copia, en el GLB materializado
    materials = load_project(job / "out").plan
    assert materials is not None and materials.ai is not None
    red = next(a for a in materials.assignments if a.material_id == "brick_red")
    assert copy.id in red.object_ids
    assert b"brick_red" in (job / "out" / "model.materialized.glb").read_bytes()
    assert len(fake.calls) == 1  # aplicar y confirmar no consultan


@node_tools
def test_edited_and_materialized_glb_are_valid(job: Path, tmp_path: Path) -> None:
    run(job, ask(service(FakeProvider(multi)), job))
    for name in ("model.current.glb", "model.materialized.glb"):
        target = tmp_path / name
        target.write_bytes((job / "out" / name).read_bytes())
        (report,) = validate([target])
        assert report["errors"] == 0, (name, report["messages"][:3])
        assert report["warnings"] == 0, (name, report["messages"][:3])


def test_instances_scaled_and_rotated_move_and_rotate(job: Path) -> None:
    before = {
        k: (o.x, o.y, o.rotation)
        for k, o in ((o.label, o) for o in load_project(job / "out").objects)
        if k.startswith("KIOSCO")
    }
    fake = FakeProvider(
        lambda ctx: plan(
            op("op1", "object.move", {"dxM": 5, "dyM": 3}, targets(refs(ctx, "KIOSCO #3"))),
            op("op2", "object.rotate", {"angleDeg": 45}, targets(refs(ctx, "KIOSCO #4"))),
        )
    )
    run(job, ask(service(fake), job))
    after = {o.label: o for o in load_project(job / "out").objects}
    assert after["KIOSCO #3"].x - before["KIOSCO #3"][0] == pytest.approx(5, abs=0.01)
    assert after["KIOSCO #3"].y - before["KIOSCO #3"][1] == pytest.approx(3, abs=0.01)
    assert after["KIOSCO #3"].width == 8.0  # conserva la escala
    assert after["KIOSCO #4"].rotation == pytest.approx(135.0, abs=0.1)  # 90 + 45
    assert after["KIOSCO #4"].x == pytest.approx(
        before["KIOSCO #4"][0], abs=0.01
    )  # gira en su sitio
    assert after["KIOSCO #1"].x == pytest.approx(before["KIOSCO #1"][0])  # las demás, intactas


def test_failure_rolls_back_everything(job: Path) -> None:
    fake = FakeProvider(
        lambda ctx: plan(
            op("op1", "object.move", {"dxM": 5, "dyM": 0}, targets(refs(ctx, "E-001"))),
            op(
                "op2",
                "building.set_height",
                {"mode": "absolute_m", "value": 0.5, "floorHeightM": None},
                targets(refs(ctx, "E-002")),
            ),
        )
    )
    proposal = ask(service(fake), job)
    before = (job / "out" / "model.glb").read_bytes()
    with pytest.raises(AiError) as caught:
        run(job, proposal)
    results = {r["id"]: r["status"] for r in caught.value.extra["results"]}
    assert results == {"op1": "not_applied", "op2": "failed"}
    assert load_project(job / "out").state.current == "rev-000"
    assert not (job / "out" / "revisions").exists()
    assert (job / "out" / "model.glb").read_bytes() == before


def test_dependencies_and_independent_subsets(job: Path) -> None:
    fake = FakeProvider(
        lambda ctx: plan(
            op("op1", "object.move", {"dxM": 5, "dyM": 0}, targets(refs(ctx, "E-001"))),
            op(
                "op2",
                "object.rotate",
                {"angleDeg": 10},
                targets(refs(ctx, "E-001")),
                depends=["op1"],
            ),
            op(
                "op3",
                "material.assign",
                {"surface": "roof", "materialId": "roof_gravel"},
                targets(refs(ctx, "E-002")),
            ),
        )
    )
    proposal = ask(service(fake), job)
    with pytest.raises(AiError) as caught:
        run(job, proposal, ["op2"])
    assert caught.value.kind == "dependency"
    x0 = facts(job, "E-001").x
    result = run(job, proposal, ["op3"])  # subconjunto independiente
    assert [r["id"] for r in result["results"]] == ["op3"]
    assert facts(job, "E-001").x == x0  # lo no marcado no se toca


def test_pending_is_resolved_by_selection_without_another_call(job: Path) -> None:
    from extrude.ai_providers import SimulatedProvider

    sim = SimulatedProvider()
    svc = service(sim)
    proposal = ask(svc, job, "Sube la torre central dos plantas")
    q = proposal["pending"][0]["id"]
    with pytest.raises(AiError) as caught:
        run(job, proposal)
    assert caught.value.kind == "needs_resolution"
    run(job, proposal, resolutions={q: ids_of(job, "E-006")})
    assert facts(job, "E-006").height == 18.0
    with pytest.raises(AiError) as caught:  # una copia de bloque no se acepta como objetivo
        proposal2 = ask(svc, job, "Sube la torre central dos plantas", baseRevision="rev-001")
        run(job, proposal2, resolutions={proposal2["pending"][0]["id"]: ids_of(job, "KIOSCO #1")})
    assert "no es compatible" in caught.value.problem


def test_missing_floor_height_is_given_by_the_user(job: Path) -> None:
    without_floor_height(job)
    fake = FakeProvider(
        lambda ctx: plan(
            op(
                "op1",
                "building.set_height",
                {"mode": "delta_floors", "value": 2, "floorHeightM": None},
                targets(refs(ctx, "E-001")),
            ),
        )
    )
    proposal = ask(service(fake), job)
    with pytest.raises(AiError) as caught:
        run(job, proposal)
    assert caught.value.kind == "needs_input"
    run(job, proposal, inputs={"op1": {"floorHeightM": 3.5}})
    assert roof_height(job, "E-001") == pytest.approx(19.0, abs=0.01)


def test_stale_plans_are_refused(job: Path) -> None:
    fake = FakeProvider(
        lambda ctx: plan(
            op("op1", "object.move", {"dxM": 1, "dyM": 0}, targets(refs(ctx, "E-001"))),
            op("op2", "object.move", {"dxM": 1, "dyM": 0}, targets(refs(ctx, "E-002"))),
        )
    )
    proposal = ask(service(fake), job)
    run(job, proposal, ["op1"])
    with pytest.raises(AiError) as caught:
        run(job, proposal, ["op2"], base="rev-001")
    assert caught.value.kind == "stale_plan" and "rev-000" in caught.value.problem


def test_manual_materials_are_protected_and_advance_the_revision(job: Path) -> None:
    project = load_project(job / "out")
    manual = {
        "schemaVersion": 1,
        "projectId": "visor",
        "geometryRevision": project.geometry_revision,
        "materialRevision": "mat-000",
        "catalogVersion": "library-1",
        "assignments": [
            {
                "objectIds": ids_of(job, "E-001"),
                "surface": "facade_exterior",
                "materialId": "stone_grey",
                "materialVersion": "1",
                "origin": "manual",
            }
        ],
    }
    apply_plan(job / "out", json.dumps(manual).encode(), preview=False)
    assert load_project(job / "out").state.current == "rev-001"
    fake = FakeProvider(
        lambda ctx: plan(
            op(
                "op1",
                "material.assign",
                {"surface": "facade_exterior", "materialId": "brick_red"},
                targets(kinds=["building"]),
            ),
        )
    )
    proposal = ask(service(fake), job, baseRevision="rev-001")
    assert ids_of(job, "E-001")[0] not in proposal["operations"][0]["objectIds"]
    assert any(c["source"] == "manual" for c in proposal["conflicts"])
    run(job, proposal)
    plan_now = load_project(job / "out").plan
    assert plan_now is not None
    kept = [a for a in plan_now.assignments if ids_of(job, "E-001")[0] in a.object_ids]
    assert [a.material_id for a in kept] == ["stone_grey"]


def test_undo_redo_and_reopen_without_ai(job: Path) -> None:
    fake = FakeProvider(multi)
    run(job, ask(service(fake), job))
    original = (job / "out" / "model.glb").read_bytes()
    state = step(job / "out", "job", forward=False)
    assert state["revision"] == "rev-000" and state["canRedo"]
    assert (job / "out" / "model.current.glb").read_bytes() == original
    assert not (job / "out" / "materials.json").exists()
    assert roof_height(job, "E-001") == pytest.approx(12.0, abs=0.01)
    state = step(job / "out", "job", forward=True)
    assert state["revision"] == "rev-001" and roof_height(job, "E-001") == pytest.approx(
        18.0, abs=0.01
    )
    # Reabrir: el estado vive en disco; leerlo no necesita proveedor ni consulta
    reopened = load_project(job / "out")
    assert reopened.state.current == "rev-001" and len(fake.calls) == 1
    assert {o.label for o in reopened.objects} >= {"E-008"}
