"""Regresiones de la auditoría de la Fase 3 (12/11/2026), hallazgos H1-H4, H6, H7 y H9.

Cada prueba reproduce el escenario del informe sobre el servicio real (DXF convertido, plan
validado, aplicación al GLB) y comprueba el resultado medido, no la implementación.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from ai_fixtures import write_model
from ai_helpers import FakeProvider, ask, copy_job, ids_of, op, plan, refs, service, targets

from extrude.ai_planner import AiError, load_project
from extrude.glb_edit import GlbEditor, locate
from extrude.materials_service import apply_plan, project_status
from extrude.project_edit import ApplyRequest, apply_edit


@pytest.fixture(scope="module")
def model_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_model(tmp_path_factory.mktemp("audit") / "out", doubles=True)


@pytest.fixture
def job(tmp_path: Path, model_dir: Path) -> Path:
    return copy_job(model_dir, tmp_path / "job")


def run(
    job: Path, proposal: dict[str, Any], ops: list[str] | None = None, **kw: Any
) -> dict[str, Any]:
    request = ApplyRequest(
        planId=proposal["planId"],
        baseRevision=proposal["baseRevision"],
        operations=ops or [o["id"] for o in proposal["operations"]],
        **kw,
    )
    return apply_edit(job, request, preview=False)


def facts(job: Path) -> dict[str, Any]:
    return {o.label: o for o in load_project(job / "out").objects}


def pending(pid: str) -> dict[str, Any]:
    return {
        "id": pid,
        "reference": "esos",
        "question": "¿Cuáles?",
        "candidates": [],
        "rule": {"kind": "none", "side": None, "by": None, "order": None, "count": None},
    }


def manual(job: Path, label: str, material: str, surface: str = "facade_exterior") -> None:
    project = load_project(job / "out")
    current = project.plan.assignments if project.plan else []
    body = {
        "schemaVersion": 1,
        "projectId": "visor",
        "geometryRevision": project.geometry_revision,
        "materialRevision": "mat-000",
        "catalogVersion": "library-1",
        "assignments": [
            a.model_dump(mode="json", by_alias=True, exclude_none=True) for a in current
        ]
        + [
            {
                "objectIds": ids_of(job, label),
                "surface": surface,
                "materialId": material,
                "materialVersion": "1",
                "origin": "manual",
            }
        ],
    }
    apply_plan(job / "out", json.dumps(body).encode(), preview=False)


# --- H1: resolver dudas revalida el conjunto ---------------------------------------------------


def test_h1_two_pendings_converging_on_one_surface_is_a_conflict(job: Path) -> None:
    fake = FakeProvider(
        lambda ctx: plan(
            op(
                "op1",
                "material.assign",
                {"surface": "facade_exterior", "materialId": "brick_light"},
                targets(pending="q1"),
            ),
            op(
                "op2",
                "material.assign",
                {"surface": "facade_exterior", "materialId": "brick_red"},
                targets(pending="q2"),
            ),
            pending=[pending("q1"), pending("q2")],
        )
    )
    proposal = ask(service(fake), job)
    e1 = ids_of(job, "E-001")
    with pytest.raises(AiError) as caught:
        run(job, proposal, resolutions={"q1": e1, "q2": e1})
    assert caught.value.kind == "conflict"
    assert load_project(job / "out").state.current == "rev-000"  # nada se aplicó


def test_h1_exception_survives_candidates(job: Path) -> None:
    x1 = facts(job)["E-001"].x
    x2 = facts(job)["E-002"].x
    fake = FakeProvider(
        lambda ctx: plan(
            op(
                "op1",
                "object.move",
                {"dxM": 10, "dyM": 0},
                targets(pending="q1", **{"except": refs(ctx, "E-001")}),
            ),
            pending=[pending("q1")],
        )
    )
    proposal = ask(service(fake), job)
    result = run(job, proposal, resolutions={"q1": ids_of(job, "E-001", "E-002")})
    after = facts(job)
    assert after["E-001"].x == pytest.approx(x1)  # la excepción del encargo se respeta
    assert after["E-002"].x == pytest.approx(x2 + 10, abs=0.01)
    assert "excepción" in result["results"][0]["message"]


def test_h1_manual_assignment_still_wins_after_resolving(job: Path) -> None:
    manual(job, "E-001", "stone_grey")
    fake = FakeProvider(
        lambda ctx: plan(
            op(
                "op1",
                "material.assign",
                {"surface": "facade_exterior", "materialId": "brick_red"},
                targets(pending="q1"),
            ),
            pending=[pending("q1")],
        )
    )
    proposal = ask(service(fake), job, baseRevision="rev-001")
    result = run(job, proposal, resolutions={"q1": ids_of(job, "E-001", "E-002")})
    materials = facts(job)
    assert materials["E-001"].materials["facade_exterior"] == "stone_grey"
    assert materials["E-002"].materials["facade_exterior"] == "brick_red"
    assert "asignación manual" in result["results"][0]["message"]


# --- H2 y H6: bloques de varias piezas ---------------------------------------------------------


def test_h2_moving_two_pieces_of_one_copy_moves_it_once(job: Path) -> None:
    before = facts(job)
    fake = FakeProvider(
        lambda ctx: plan(
            op(
                "op1",
                "object.move",
                {"dxM": 5, "dyM": 0},
                targets(refs(ctx, "DOBLE #1.1", "DOBLE #1.2")),
            ),
        )
    )
    proposal = ask(service(fake), job)
    assert "copia de bloque completa" in " ".join(proposal["operations"][0]["messages"])
    result = run(job, proposal)
    after = facts(job)
    for label in ("DOBLE #1.1", "DOBLE #1.2"):
        assert after[label].x - before[label].x == pytest.approx(5.0, abs=0.01)
    assert after["DOBLE #2.1"].x == pytest.approx(before["DOBLE #2.1"].x)
    assert set(result["results"][0]["objectIds"]) == set(ids_of(job, "DOBLE #1.1", "DOBLE #1.2"))


def test_h6_duplicating_a_copy_keeps_materials_per_piece(job: Path) -> None:
    manual(job, "DOBLE #1.1", "brick_red")
    manual(job, "DOBLE #1.2", "brick_light")
    fake = FakeProvider(
        lambda ctx: plan(
            op(
                "op1",
                "object.duplicate",
                {"dxM": 0, "dyM": -30, "copies": 1},
                targets(refs(ctx, "DOBLE #1.1")),
            ),
        )
    )
    proposal = ask(service(fake), job, baseRevision="rev-002")
    run(job, proposal)
    after = facts(job)
    copies = [label for label in after if label.startswith("DOBLE #5")]
    assert sorted(copies) == ["DOBLE #5.1", "DOBLE #5.2"]
    assert after["DOBLE #5.1"].materials["facade_exterior"] == "brick_red"
    assert after["DOBLE #5.2"].materials["facade_exterior"] == "brick_light"


# --- H3: alturas encadenadas -------------------------------------------------------------------


def test_h3_dependent_heights_use_the_working_state(job: Path) -> None:
    fake = FakeProvider(
        lambda ctx: plan(
            op(
                "op1",
                "building.set_height",
                {"mode": "delta_m", "value": 3, "floorHeightM": None},
                targets(refs(ctx, "E-001")),
            ),
            op(
                "op2",
                "building.set_height",
                {"mode": "delta_m", "value": 4, "floorHeightM": None},
                targets(refs(ctx, "E-001")),
                depends=["op1"],
            ),
        )
    )
    proposal = ask(service(fake), job)
    impacts = [o["impact"]["heights"][0] for o in proposal["operations"]]
    assert [(i["from"], i["to"]) for i in impacts] == [(12.0, 15.0), (15.0, 19.0)]
    run(job, proposal)
    assert facts(job)["E-001"].height == pytest.approx(19.0)
    editor = GlbEditor((job / "out" / "model.current.glb").read_bytes())
    where = locate(editor.doc)[ids_of(job, "E-001")[0]]
    mesh = editor.doc["meshes"][editor.doc["nodes"][where.node]["mesh"]]["primitives"][
        where.prims[0]
    ]
    mask = np.rint(editor.read(mesh["attributes"]["_OBJECT_ID"])).astype(int) == where.index
    pos = editor.read(mesh["attributes"]["POSITION"])[mask]
    assert float(np.ptp(pos[:, 1])) == pytest.approx(19.0, abs=0.01)  # medido en el GLB


# --- H4 y H7: efectos del visor en la revisión -------------------------------------------------


SUN = {
    "mode": "datetime",
    "isoDate": "2024-06-21T18:30:00+02:00",
    "lat": 40.4,
    "lon": -3.7,
    "azimuthDeg": 0,
    "elevationDeg": 0,
    "northRotationDeg": 0,
    "intensity": 3,
    "colorTempK": 5800,
}


def mixed(ctx: dict[str, Any]) -> str:
    return plan(
        op("op1", "object.move", {"dxM": 5, "dyM": 0}, targets(refs(ctx, "E-001"))),
        op("op2", "scene.set_sun", {"time": "18:30", "date": None}, targets()),
    )


def test_h4_viewer_effects_must_be_prepared_before_committing(job: Path) -> None:
    proposal = ask(service(FakeProvider(mixed)), job)
    with pytest.raises(AiError) as caught:
        run(job, proposal)
    assert caught.value.kind == "viewer_unprepared"
    assert load_project(job / "out").state.current == "rev-000"  # ni la geometría se confirmó


def test_h7_viewer_state_is_persisted_with_the_revision(job: Path) -> None:
    proposal = ask(service(FakeProvider(mixed)), job)
    viewer = {
        "views": [],
        "sunBefore": {**SUN, "isoDate": "2024-06-21T12:00:00+02:00"},
        "sunAfter": SUN,
    }
    run(job, proposal, viewer=viewer)
    reopened = project_status(job / "out", "job")  # como tras reiniciar: solo disco
    head = next(r for r in reopened["history"] if r["id"] == reopened["revision"])
    assert head["viewer"]["sunAfter"]["isoDate"] == "2024-06-21T18:30:00+02:00"
    assert head["viewer"]["sunBefore"]["isoDate"].endswith("12:00:00+02:00")


def test_h7_prepared_views_must_match_the_plan(job: Path) -> None:
    fake = FakeProvider(
        lambda ctx: plan(
            op(
                "op1",
                "view.create",
                {"name": "Sur", "preset": "south", "time": None, "date": None},
                targets(),
            ),
        )
    )
    proposal = ask(service(fake), job)
    with pytest.raises(AiError) as caught:
        run(job, proposal, viewer={"views": [], "sunBefore": None, "sunAfter": None})
    assert caught.value.kind == "viewer_invalid"


# --- H9: estado inicial ------------------------------------------------------------------------


def test_h9_initial_state_announces_an_existing_model(job: Path) -> None:
    status = project_status(job / "out", "job")
    name = status["modelUrl"].rsplit("/", 1)[-1]
    assert status["revision"] == "rev-000" and (job / "out" / name).is_file()
