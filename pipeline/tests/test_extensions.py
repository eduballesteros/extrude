"""Ampliaciones de edición (entorno, fachadas, retranqueo, copias independientes, cubiertas) y
cierre de los problemas previos. Comprueba resultados medidos en el GLB y en el proyecto."""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from ai_fixtures import write_model
from ai_helpers import FakeProvider, ask, copy_job, ids_of, op, plan, refs, service, targets

from extrude.ai_planner import AiError, load_project
from extrude.arch_ops import hosted
from extrude.glb_edit import GlbEditor, locate
from extrude.gltf_export import INSTANCING_EXT
from extrude.materials_service import StaleRevision, apply_plan, step
from extrude.node_tools import tools_dir, validate
from extrude.project_edit import ApplyRequest, apply_edit
from extrude.surfaces import Surface

node_tools = pytest.mark.skipif(
    shutil.which("node") is None or not (tools_dir() / "node_modules" / "gltf-validator").is_dir(),
    reason="Node o tools/node_modules no disponibles",
)


@pytest.fixture(scope="module")
def model_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_model(tmp_path_factory.mktemp("ext") / "out", doubles=True)


@pytest.fixture
def job(tmp_path: Path, model_dir: Path) -> Path:
    return copy_job(model_dir, tmp_path / "job")


def revision(job: Path) -> str:
    return load_project(job / "out").state.current


CALLS: list[int] = []


def run(
    job: Path, responder: Any, *, preview: bool = False, ops: list[str] | None = None, **kw: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    fake = FakeProvider(responder)
    CALLS.append(1)  # descripción distinta: cada petición es nueva para la caché
    proposal = ask(service(fake), job, f"petición {len(CALLS)}", baseRevision=revision(job))
    request = ApplyRequest(
        planId=proposal["planId"],
        baseRevision=proposal["baseRevision"],
        operations=ops or [o["id"] for o in proposal["operations"]],
        **kw,
    )
    result = apply_edit(job, request, preview=preview)
    assert (
        len(fake.calls) <= 1
    )  # aplicar no vuelve a consultar (y la caché puede evitar la llamada)
    return proposal, result


def facts(job: Path) -> dict[str, Any]:
    return {o.label: o for o in load_project(job / "out").objects}


def trees(ctx: dict[str, Any], **extra: Any) -> str:
    args = {
        "assetId": "tree_round",
        "layout": "row",
        "count": 10,
        "spacingM": 5.0,
        "sides": "left",
        "sideOffsetM": 2.0,
        "facing": "along",
        "rotationDeg": None,
        "scale": 1.0,
        "xM": None,
        "yM": None,
        "support": "elevation",
        "baseZM": None,
        **extra,
    }
    return plan(op("op1", "site.place", args, targets(refs(ctx, "V-001"))))


def windows(orientation: str = "south", floors: list[int] | None = None) -> Any:
    args = {
        "orientation": orientation,
        "widthM": 1.2,
        "heightM": 1.5,
        "sillM": 0.9,
        "depthM": 0.1,
        "spacingM": 1.0,
        "columns": None,
        "floors": floors,
        "floorHeightM": None,
        "glassMaterialId": None,
        "frameMaterialId": None,
    }
    return lambda ctx: plan(op("op1", "facade.add_windows", args, targets(refs(ctx, "E-001"))))


def instance_positions(job: Path, block: str) -> np.ndarray:
    editor = GlbEditor((job / "out" / "model.current.glb").read_bytes())
    node = next(n for n in editor.doc["nodes"] if n.get("extras", {}).get("block") == block)
    t = editor.read(node["extensions"][INSTANCING_EXT]["attributes"]["TRANSLATION"])
    return np.column_stack([t[:, 0], -t[:, 2], t[:, 1]])


# --- Vegetación y mobiliario -----------------------------------------------------------------


def test_trees_along_a_road_need_explicit_support_then_persist_undo_and_redo(job: Path) -> None:
    proposal = ask(service(FakeProvider(trees)), job)
    assert proposal["operations"][0]["status"] == "needs_input"  # no hay terreno: se pide cota
    with pytest.raises(AiError) as caught:
        apply_edit(
            job,
            ApplyRequest(planId=proposal["planId"], baseRevision="rev-000", operations=["op1"]),
            preview=False,
        )
    assert caught.value.kind == "needs_input"
    _, shown = run(job, trees, preview=True, inputs={"op1": {"baseZM": 0.0}})
    assert revision(job) == "rev-000" and shown["preview"]  # previsualizar no confirma
    run(job, trees, inputs={"op1": {"baseZM": 0.0}})
    placed = instance_positions(job, "TREE_ROUND")
    assert len(placed) == 10
    steps = np.linalg.norm(np.diff(placed[:, :2], axis=0), axis=1)
    assert steps == pytest.approx(np.full(9, 5.0), abs=0.01)
    assert placed[:, 2] == pytest.approx(np.zeros(10), abs=0.01)  # cota dada (origen z = 0)
    assert len([o for o in facts(job).values() if o.kind == "site"]) == 10
    step(job / "out", "job", forward=False)
    assert not [o for o in facts(job).values() if o.kind == "site"]
    step(job / "out", "job", forward=True)
    assert len([o for o in facts(job).values() if o.kind == "site"]) == 10


def test_benches_around_a_square_face_its_centre(job: Path) -> None:
    def benches(ctx: dict[str, Any]) -> str:
        return plan(
            op(
                "op1",
                "site.place",
                {
                    "assetId": "bench",
                    "layout": "around",
                    "count": 4,
                    "spacingM": None,
                    "sides": "center",
                    "sideOffsetM": 3.0,
                    "facing": "target",
                    "rotationDeg": None,
                    "scale": 1.0,
                    "xM": None,
                    "yM": None,
                    "support": "elevation",
                    "baseZM": 0.0,
                },
                targets(refs(ctx, "E-006")),
            )
        )

    run(job, benches)
    editor = GlbEditor((job / "out" / "model.current.glb").read_bytes())
    node = next(n for n in editor.doc["nodes"] if n.get("extras", {}).get("block") == "BENCH")
    attrs = node["extensions"][INSTANCING_EXT]["attributes"]
    t = editor.read(attrs["TRANSLATION"])
    r = editor.read(attrs["ROTATION"])
    centre = np.array([facts(job)["E-006"].x, facts(job)["E-006"].y])
    for pos, q in zip(t, r, strict=True):
        yaw = 2 * math.atan2(q[1], q[3])
        front = np.array([-math.sin(yaw), math.cos(yaw)])  # +Y local girado en planta
        to_centre = centre - np.array([pos[0], -pos[2]])
        assert float(np.dot(front, to_centre / np.linalg.norm(to_centre))) > 0.95


def test_site_elements_move_and_delete(job: Path) -> None:
    run(job, trees, inputs={"op1": {"baseZM": 0.0}})
    first = next(o for o in facts(job).values() if o.label == "TREE_ROUND #1")
    run(
        job,
        lambda ctx: plan(
            op("op1", "object.move", {"dxM": 3, "dyM": 0}, targets(refs(ctx, "TREE_ROUND #1")))
        ),
    )
    assert facts(job)["TREE_ROUND #1"].x == pytest.approx(first.x + 3, abs=0.01)
    run(
        job,
        lambda ctx: plan(
            op("op1", "object.delete", {"confirm": True}, targets(refs(ctx, "TREE_ROUND #2")))
        ),
    )
    assert len([o for o in facts(job).values() if o.kind == "site"]) == 9


# --- Ventanas y balcones -------------------------------------------------------------------------


def test_windows_then_height_changes(job: Path) -> None:
    run(job, windows("south"))
    added = [o for o in facts(job).values() if o.kind == "facade_element"]
    assert {o.layer for o in added} == {"VENTANAS", "CARPINTERIAS"}
    plan_now = load_project(job / "out").plan
    assert plan_now is not None and {a.material_id for a in plan_now.assignments} >= {
        "glass_blue",
        "metal_dark",
    }
    glass = next(o for o in added if o.layer == "VENTANAS")
    assert glass.y < facts(job)["E-001"].y  # sobre la fachada sur
    with pytest.raises(AiError) as caught:  # duplicado involuntario
        run(job, windows("south"))
    assert "Ya hay ventanas" in caught.value.problem
    # Subir el edificio: las ventanas siguen; bajarlo por debajo de ellas es un conflicto
    run(
        job,
        lambda ctx: plan(
            op(
                "op1",
                "building.set_height",
                {"mode": "delta_m", "value": 3, "floorHeightM": None},
                targets(refs(ctx, "E-001")),
            )
        ),
    )
    with pytest.raises(AiError) as caught:
        run(
            job,
            lambda ctx: plan(
                op(
                    "op1",
                    "building.set_height",
                    {"mode": "absolute_m", "value": 4, "floorHeightM": None},
                    targets(refs(ctx, "E-001")),
                )
            ),
        )
    assert "por encima de la nueva altura" in caught.value.problem


def test_windows_follow_their_building_when_moved(job: Path) -> None:
    run(job, windows("south"))
    before = {o.label: o.x for o in facts(job).values() if o.kind == "facade_element"}
    run(
        job,
        lambda ctx: plan(
            op("op1", "object.move", {"dxM": 10, "dyM": 0}, targets(refs(ctx, "E-001")))
        ),
    )
    after = facts(job)
    for label, x in before.items():
        assert after[label].x == pytest.approx(x + 10, abs=0.01)


def test_balconies_on_given_floors(job: Path) -> None:
    run(
        job,
        lambda ctx: plan(
            op(
                "op1",
                "facade.add_balconies",
                {
                    "orientation": "north",
                    "widthM": 3.0,
                    "projectionM": 1.5,
                    "thicknessM": 0.2,
                    "railingHeightM": 1.0,
                    "floors": [2, 3],
                    "floorHeightM": None,
                    "materialId": None,
                },
                targets(refs(ctx, "E-001")),
            )
        ),
    )
    balcony = next(o for o in facts(job).values() if o.layer == "BALCONES")
    assert balcony.y > facts(job)["E-001"].y + 4.9  # vuela hacia el norte
    with pytest.raises(AiError):  # planta baja no
        run(
            job,
            lambda ctx: plan(
                op(
                    "op1",
                    "facade.add_balconies",
                    {
                        "orientation": "east",
                        "widthM": 3.0,
                        "projectionM": 1.5,
                        "thicknessM": 0.2,
                        "railingHeightM": 1.0,
                        "floors": [0],
                        "floorHeightM": None,
                        "materialId": None,
                    },
                    targets(refs(ctx, "E-001")),
                )
            ),
        )


# --- Retranqueo ------------------------------------------------------------------------------


def test_setback_with_courtyard_keeps_materials_and_rejects_vanishing(job: Path) -> None:
    project = load_project(job / "out")
    body = {
        "schemaVersion": 1,
        "projectId": "visor",
        "geometryRevision": project.geometry_revision,
        "materialRevision": "mat-000",
        "catalogVersion": "library-1",
        "assignments": [
            {
                "objectIds": ids_of(job, "E-006"),
                "surface": "facade_exterior",
                "materialId": "brick_red",
                "materialVersion": "1",
                "origin": "manual",
            }
        ],
    }
    apply_plan(job / "out", json.dumps(body).encode(), preview=False)

    def attic(ctx: dict[str, Any], d: float = 2.0) -> str:
        return plan(
            op(
                "op1",
                "building.setback",
                {"startHeightM": 9, "setbackM": d, "orientations": ["north"], "upperHeightM": 3},
                targets(refs(ctx, "E-006")),
            )
        )

    run(job, attic)
    after = facts(job)
    assert after["E-006"].height == pytest.approx(9.0)
    upper = after["E-006.A1"]
    assert upper.height == pytest.approx(3.0)
    assert upper.depth == pytest.approx(after["E-006"].depth - 2.0, abs=0.05)  # 2 m al norte
    assert "facade_courtyard" in upper.surfaces  # el patio sigue en el ático
    assert upper.materials["facade_exterior"] == "brick_red"  # hereda materiales
    with pytest.raises(AiError) as caught:
        run(
            job,
            lambda ctx: (
                attic(ctx, 25.0)
                if False
                else plan(
                    op(
                        "op1",
                        "building.setback",
                        {
                            "startHeightM": 6,
                            "setbackM": 25,
                            "orientations": ["all"],
                            "upperHeightM": 3,
                        },
                        targets(refs(ctx, "E-001")),
                    )
                )
            ),
        )
    assert "desaparece" in caught.value.problem


# --- Copias de bloque independientes -------------------------------------------------------


def test_make_one_copy_unique_then_raise_it(job: Path) -> None:
    before = facts(job)
    run(
        job,
        lambda ctx: plan(
            op("op1", "block.make_unique", {"confirm": True}, targets(refs(ctx, "DOBLE #2.1"))),
            op(
                "op2",
                "building.set_height",
                {"mode": "delta_m", "value": 3, "floorHeightM": None},
                targets(refs(ctx, "DOBLE #2.1", "DOBLE #2.2")),
                depends=["op1"],
            ),
        ),
    )
    after = facts(job)
    for label in ("DOBLE #2.1", "DOBLE #2.2"):
        assert not after[label].instance and after[label].id == before[label].id  # misma identidad
        assert after[label].height == pytest.approx(before[label].height + 3, abs=0.01)
        assert after[label].x == pytest.approx(before[label].x, abs=0.01)  # TRS conservado
    for label in ("DOBLE #1.1", "DOBLE #3.2"):
        assert after[label].instance and after[label].height == pytest.approx(before[label].height)


# --- Cubiertas inclinadas ------------------------------------------------------------------


def make_sloped(job: Path, label: str = "E-001") -> None:
    """Convierte la cubierta plana de `label` en una cubierta a un agua (sube 0,3 m por metro)."""
    data = (job / "out" / "model.glb").read_bytes()
    editor = GlbEditor(data)
    where = locate(editor.doc)[ids_of(job, label)[0]]
    node = editor.doc["nodes"][where.node]
    for prim in where.prims:
        a = editor.doc["meshes"][node["mesh"]]["primitives"][prim]["attributes"]
        mask = np.rint(editor.read(a["_OBJECT_ID"])).astype(int) == where.index
        pos = editor.read(a["POSITION"])
        top = pos[mask, 1].max()
        lifted = mask & (np.abs(pos[:, 1] - top) < 1e-3)
        pos[lifted, 1] += 0.3 * (pos[lifted, 0] - pos[mask, 0].min())
        editor.write(a["POSITION"], pos)
    (job / "out" / "model.glb").write_bytes(editor.encode())


def roof_profile(job: Path, label: str) -> tuple[float, float]:
    editor = GlbEditor((job / "out" / "model.current.glb").read_bytes())
    where = locate(editor.doc)[ids_of(job, label)[0]]
    node = editor.doc["nodes"][where.node]
    ys = []
    for prim in where.prims:
        a = editor.doc["meshes"][node["mesh"]]["primitives"][prim]["attributes"]
        mask = np.rint(editor.read(a["_OBJECT_ID"])).astype(int) == where.index
        roof = mask & (np.rint(editor.read(a["_SURFACE"])).astype(int) == Surface.ROOF)
        ys.append(editor.read(a["POSITION"])[roof, 1])
    y = np.concatenate(ys)
    return float(y.min()), float(np.ptp(y))


def test_sloped_roof_raise_walls_and_ridge(job: Path) -> None:
    make_sloped(job)
    assert not facts(job)["E-001"].flat_roof
    eave, ridge = 12.0, 3.0  # 10 m de ancho × 0,3
    run(
        job,
        lambda ctx: plan(
            op("op1", "building.raise_walls", {"deltaM": 2.0}, targets(refs(ctx, "E-001")))
        ),
    )
    low, span = roof_profile(job, "E-001")
    assert span == pytest.approx(ridge, abs=0.01)  # la cubierta conserva su forma
    run(
        job,
        lambda ctx: plan(
            op("op1", "roof.set_ridge", {"ridgeHeightM": 1.5}, targets(refs(ctx, "E-001")))
        ),
    )
    low2, span2 = roof_profile(job, "E-001")
    assert low2 == pytest.approx(low) and span2 == pytest.approx(1.5, abs=0.01)
    assert eave  # alero fijo comprobado con low2 == low
    flat = ask(
        service(
            FakeProvider(
                lambda ctx: plan(
                    op("op1", "roof.set_ridge", {"ridgeHeightM": 2.0}, targets(refs(ctx, "E-002")))
                )
            )
        ),
        job,
        baseRevision=revision(job),
    )
    assert flat["operations"][0]["status"] == "incompatible"  # cubierta plana: sin cumbrera


@node_tools
def test_extended_model_is_a_valid_glb(job: Path, tmp_path: Path) -> None:
    run(job, trees, inputs={"op1": {"baseZM": 0.0}})
    run(job, windows("south"))
    run(
        job,
        lambda ctx: plan(
            op("op1", "block.make_unique", {"confirm": True}, targets(refs(ctx, "DOBLE #3.1")))
        ),
    )
    for name in ("model.current.glb", "model.materialized.glb"):
        target = tmp_path / name
        target.write_bytes((job / "out" / name).read_bytes())
        (report,) = validate([target])
        assert report["errors"] == 0, (name, report["messages"][:3])


# --- Problemas previos -----------------------------------------------------------------------


def test_excluded_piece_blocks_moving_its_whole_copy(job: Path) -> None:
    proposal = ask(
        service(
            FakeProvider(
                lambda ctx: plan(
                    op(
                        "op1",
                        "object.move",
                        {"dxM": 5, "dyM": 0},
                        targets(refs(ctx, "DOBLE #1.1"), **{"except": refs(ctx, "DOBLE #1.2")}),
                    )
                )
            )
        ),
        job,
    )
    assert proposal["operations"][0]["status"] == "blocked"
    assert "make_unique" in " ".join(proposal["operations"][0]["messages"])


def test_viewer_contract_rejects_empty_or_mismatched_sun(job: Path) -> None:
    def sun(ctx: dict[str, Any]) -> str:
        return plan(op("op1", "scene.set_sun", {"time": "18:30", "date": None}, targets()))

    for bad in ({}, {"views": [], "sunBefore": None, "sunAfter": None}):
        with pytest.raises(AiError) as caught:
            run(job, sun, viewer=bad)
        assert caught.value.kind == "viewer_invalid"
    state = {
        "mode": "datetime",
        "isoDate": "2024-06-21T12:00:00+02:00",
        "lat": 40,
        "lon": -3,
        "azimuthDeg": 0,
        "elevationDeg": 10,
        "northRotationDeg": 0,
        "intensity": 3,
        "colorTempK": 5800,
    }
    with pytest.raises(AiError) as caught:  # hora preparada distinta de la pedida
        run(job, sun, viewer={"views": [], "sunBefore": state, "sunAfter": state})
    assert "18:30" in caught.value.problem
    run(
        job,
        sun,
        viewer={
            "views": [],
            "sunBefore": state,
            "sunAfter": {**state, "isoDate": "2024-06-21T18:30:00+02:00"},
        },
    )
    assert revision(job) == "rev-001"


def test_targeted_undo_never_undoes_a_later_revision(job: Path) -> None:
    run(job, windows("south"))
    run(job, windows("north"))
    with pytest.raises(StaleRevision):
        step(job / "out", "job", forward=False, expect="rev-001")
    assert revision(job) == "rev-002"
    step(job / "out", "job", forward=False, expect="rev-002")
    assert revision(job) == "rev-001"


# --- Simulador local (sin red): mismas peticiones en lenguaje natural ------------------------


def simulate(job: Path, text: str, **kw: Any) -> dict[str, Any]:
    from extrude.ai_planner import AiService, ProposalRequest
    from extrude.ai_providers import SimulatedProvider

    svc = AiService(providers={"simulated": SimulatedProvider()})
    request = ProposalRequest.model_validate(
        {
            "provider": "simulated",
            "description": text,
            "consent": True,
            "baseRevision": revision(job),
        }
    )
    proposal = svc.propose("job1", job, request)
    apply_edit(
        job,
        ApplyRequest(
            planId=proposal["planId"],
            baseRevision=proposal["baseRevision"],
            operations=[o["id"] for o in proposal["operations"]],
            **kw,
        ),
        preview=False,
    )
    return proposal


def test_simulator_places_trees_with_a_given_elevation(job: Path) -> None:
    proposal = simulate(
        job, "Coloca 10 árboles a lo largo de V-001 cada 5 m", inputs={"op1": {"baseZM": 0.0}}
    )
    assert [o["tool"] for o in proposal["operations"]] == ["site.place"]
    assert len(instance_positions(job, "TREE_ROUND")) == 10


def test_simulator_unique_copy_then_raise_whole_copy(job: Path) -> None:
    proposal = simulate(job, "Independiza DOBLE #2.1 y súbela 3 m")
    assert proposal["operations"][1]["dependsOn"] == ["op1"]
    after = facts(job)
    assert after["DOBLE #2.1"].height == pytest.approx(9.0)
    assert after["DOBLE #2.2"].height == pytest.approx(9.0)
    assert after["DOBLE #1.1"].instance


def test_simulator_reports_real_openings_as_planned(job: Path) -> None:
    from extrude.ai_planner import AiService, ProposalRequest
    from extrude.ai_providers import SimulatedProvider

    svc = AiService(providers={"simulated": SimulatedProvider()})
    proposal = svc.propose(
        "job1",
        job,
        ProposalRequest.model_validate(
            {
                "provider": "simulated",
                "description": "Abre huecos en la fachada de E-002",
                "consent": True,
            }
        ),
    )
    assert not proposal["operations"]
    assert proposal["unsupported"][0]["capability"] == "facade.openings_cut"


@node_tools
@pytest.mark.parametrize("another_facade", [False, True])
def test_delete_windows_prunes_geometry_and_materials(job: Path, another_facade: bool) -> None:
    run(job, windows("south", [1]))
    if another_facade:
        run(job, windows("north", [1]))
    selected = [o for o in facts(job).values() if o.kind == "facade_element" and "south" in o.label]
    labels, removed = [o.label for o in selected], {o.id for o in selected}
    run(
        job,
        lambda ctx: plan(
            op("op1", "object.delete", {"confirm": True}, targets(refs(ctx, *labels)))
        ),
    )
    project = load_project(job / "out")
    assert removed.isdisjoint(o.id for o in project.objects)
    assert project.plan is not None
    assert removed.isdisjoint(i for a in project.plan.assignments for i in a.object_ids)
    (report,) = validate([job / "out" / "model.materialized.glb"])
    assert report["errors"] == 0, report["messages"][:3]
    # La capa vacía puede volver a utilizarse sin índices desfasados ni detección de duplicados.
    run(job, windows("south", [1]))
    assert len([o for o in facts(job).values() if "ventanas south" in o.label]) == 1


def test_duplicate_building_includes_hosted_elements_and_materials(job: Path) -> None:
    run(job, windows("south", [1]))
    before = load_project(job / "out")
    original = next(o for o in before.objects if o.label == "E-001")
    original_editor = GlbEditor(before.data)
    original_elements = hosted(original_editor, original.id)
    _, result = run(
        job,
        lambda ctx: plan(
            op(
                "op1",
                "object.duplicate",
                {"dxM": 40, "dyM": 0, "copies": 1},
                targets(refs(ctx, "E-001")),
            )
        ),
    )
    project = load_project(job / "out")
    created = result["results"][0]["details"][0]["created"]
    copied_host = next(o for o in project.objects if o.id in created and o.kind == "building")
    assert copied_host.x == pytest.approx(original.x + 40, abs=0.01)
    elements = hosted(GlbEditor(project.data), copied_host.id)
    assert len(elements) == len(original_elements) == 2
    assert {e.object_id for e, _ in elements}.isdisjoint(e.object_id for e, _ in original_elements)
    for element, row in elements:
        assert row["host"] == copied_host.id and row["orientation"] == "south"
        item = next(o for o in project.objects if o.id == element.object_id)
        source = next(o for o in before.objects if o.layer == item.layer)
        assert item.x == pytest.approx(source.x + 40, abs=0.01)
        assert item.materials == source.materials
    step(job / "out", "job", forward=False)
    assert len(hosted(GlbEditor(load_project(job / "out").data), original.id)) == 2
    assert copied_host.id not in {o.id for o in load_project(job / "out").objects}
    step(job / "out", "job", forward=True)
    assert len(hosted(GlbEditor(load_project(job / "out").data), copied_host.id)) == 2


def test_changing_ridge_updates_roof_normals(job: Path) -> None:
    make_sloped(job)
    run(
        job,
        lambda ctx: plan(
            op(
                "op1",
                "roof.set_ridge",
                {"ridgeHeightM": 1.5},
                targets(refs(ctx, "E-001")),
            )
        ),
    )
    project = load_project(job / "out")
    editor = GlbEditor(project.data)
    where = locate(editor.doc)[next(o.id for o in project.objects if o.label == "E-001")]
    checked = 0
    for prim_index in where.prims:
        prim = editor.doc["meshes"][editor.doc["nodes"][where.node]["mesh"]]["primitives"][
            prim_index
        ]
        attrs = prim["attributes"]
        positions, normals = editor.read(attrs["POSITION"]), editor.read(attrs["NORMAL"])
        ids, surfaces = editor.read(attrs["_OBJECT_ID"]), editor.read(attrs["_SURFACE"])
        faces = editor.read(prim["indices"]).reshape(-1, 3).astype(int)
        own_roof = faces[((ids[faces] == where.index) & (surfaces[faces] == Surface.ROOF)).all(1)]
        for tri in own_roof:
            geometric = np.cross(
                positions[tri[1]] - positions[tri[0]], positions[tri[2]] - positions[tri[0]]
            )
            geometric /= np.linalg.norm(geometric)
            assert normals[tri] @ geometric == pytest.approx(np.ones(3), abs=1e-5)
            checked += 1
    assert checked > 0


def test_around_layout_stays_outside_an_elongated_reference(job: Path) -> None:
    from shapely.geometry import Point, box

    reference = facts(job)["V-001"]
    area = box(
        reference.x - reference.width / 2,
        reference.y - reference.depth / 2,
        reference.x + reference.width / 2,
        reference.y + reference.depth / 2,
    )
    run(job, lambda ctx: trees(ctx, layout="around", count=4, baseZM=0.0))
    points = instance_positions(job, "TREE_ROUND")
    assert len(points) == 4
    for x, y, _ in points:
        assert not area.contains(Point(x, y))
        assert area.distance(Point(x, y)) >= 2.0 - 0.01
