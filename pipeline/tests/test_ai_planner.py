"""Planificación con IA (contrato v2): una llamada, caché, autorización, validación y estados.

Proveedores simulados; ninguna llamada facturable. Las respuestas se construyen con las
referencias del contexto recibido, como haría un modelo real.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

import pytest
from ai_fixtures import KIOSKS, write_model
from ai_helpers import FakeProvider, ask, brick, copy_job, ids_of, op, plan, refs, service, targets

from extrude.ai_planner import (
    AiError,
    AiLimits,
    Project,
    ProposalRejected,
    ProposalRequest,
    build_context,
    load_project,
)
from extrude.ai_providers import ProviderError, SimulatedProvider
from extrude.glb_edit import GlbEditor
from extrude.material_library import DEFAULT_LIBRARY


@pytest.fixture(scope="module")
def model_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_model(tmp_path_factory.mktemp("planner") / "out", kiosks=True)


@pytest.fixture
def job(tmp_path: Path, model_dir: Path) -> Path:
    return copy_job(model_dir, tmp_path / "job")


# --- Una llamada, caché e invalidación --------------------------------------------------------


def test_one_click_is_one_call_with_ids_status_and_impact(job: Path) -> None:
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
    result = ask(service(fake), job, "Sube E-001 dos plantas")
    assert len(fake.calls) == 1
    [item] = result["operations"]
    assert item["objectIds"] == ids_of(job, "E-001") and item["status"] == "ready"
    assert item["impact"]["heights"] == [{"label": "E-001", "from": 12.0, "to": 18.0}]  # 3 m/planta
    assert result["baseRevision"] == "rev-000" and result["planId"].startswith("plan-")
    assert (job / "ai" / "plans" / f"{result['planId']}.json").is_file()
    log = (job / "ai" / "log.jsonl").read_text(encoding="utf-8")
    assert '"status": "ok"' in log and "Sube" not in log  # sin la petición


def test_identical_request_uses_cache_and_changes_invalidate_it(job: Path) -> None:
    fake = FakeProvider(brick("E-001"))
    svc = service(fake)
    ask(svc, job, "Fachadas  de ladrillo claro ")
    assert ask(svc, job, "Fachadas de ladrillo claro")["cached"] is True
    assert len(fake.calls) == 1
    ask(svc, job, "Fachadas de ladrillo rojo")  # otra petición
    ask(svc, job, "Fachadas de ladrillo claro", scope="selection", selection=ids_of(job, "E-001"))
    assert len(fake.calls) == 3
    svc.clear_cache(job)
    ask(svc, job, "Fachadas de ladrillo claro")
    assert len(fake.calls) == 4


# --- Autorización ------------------------------------------------------------------------------


def test_consent_is_bound_to_provider_and_reviewed_content(job: Path) -> None:
    fake = FakeProvider(brick("E-001"))
    svc = service(fake)
    base = {"provider": "fake", "description": "Fachadas de ladrillo claro"}
    with pytest.raises(AiError) as caught:
        svc.propose("job1", job, ProposalRequest.model_validate(base))
    assert caught.value.kind == "consent" and fake.calls == []
    reviewed = svc.context_preview("job1", job, ProposalRequest.model_validate(base))
    assert fake.calls == []  # revisar no consulta
    changed = {
        **base,
        "description": "Fachadas de ladrillo rojo",
        "consent": True,
        "consentHash": reviewed["contextHash"],
    }
    with pytest.raises(AiError) as caught:
        svc.propose("job1", job, ProposalRequest.model_validate(changed))
    assert caught.value.kind == "consent_changed" and fake.calls == []
    same = {**base, "consent": True, "consentHash": reviewed["contextHash"]}
    svc.propose("job1", job, ProposalRequest.model_validate(same))
    assert len(fake.calls) == 1


def test_request_from_an_old_revision_is_refused(job: Path) -> None:
    fake = FakeProvider(brick("E-001"))
    with pytest.raises(AiError) as caught:
        ask(service(fake), job, baseRevision="rev-007")
    assert caught.value.kind == "stale_project" and fake.calls == []


# --- Contexto: estado actual, privacidad, instancias -------------------------------------------


def test_context_has_current_state_and_no_files_coordinates_or_secrets(
    job: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-SECRET")
    fake = FakeProvider(brick("E-001"))
    ask(service(fake), job)
    sent = fake.calls[0]["system"] + fake.calls[0]["user"] + json.dumps(fake.calls[0]["schema"])
    assert "SECRET" not in sent and "obj-" not in sent
    for forbidden in (".dxf", ".dwg", "model.glb", str(job), "glTF", "originOffset"):
        assert forbidden not in sent, forbidden
    numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", fake.calls[0]["user"])]
    assert max(abs(n) for n in numbers) < 1_000  # nada parecido a 431000 / 4582000
    context = json.loads(fake.calls[0]["user"])
    assert context["project"]["revision"] == "rev-000"
    assert {t["name"] for t in context["tools"]} >= {"material.assign", "building.set_height"}
    assert "facade.openings_cut" in context["planned"]
    columns = context["objects"]["columns"]
    row = dict(zip(columns, context["objects"]["rows"][0], strict=True))
    assert row["floorH"] == 3.0 and row["h"] == 12.0


def test_instances_use_their_full_transform(job: Path) -> None:
    """Regresión: el contexto ignoraba escala y giro de las copias de bloque."""
    kiosks = [o for o in load_project(job / "out").objects if o.block == "KIOSCO"]
    assert [o.instance for o in kiosks] == [True] * 4
    sizes = [(o.width, o.depth, o.rotation) for o in kiosks]
    assert sizes[0] == (4.0, 2.0, 0.0)
    assert sizes[1][2] == pytest.approx(KIOSKS[1][1], abs=0.1)
    assert sizes[2][:2] == (8.0, 4.0)  # escala 2
    assert sizes[3][:2] == (4.0, 8.0) and sizes[3][2] == pytest.approx(90.0, abs=0.1)
    # Centros reales: inserción + giro y escala del centro del prototipo (2, 1)
    xs = [o.x for o in kiosks]
    assert xs[2] - xs[0] == pytest.approx(
        40 + 2, abs=0.05
    )  # 40 m entre inserciones + (2·2 - 2) de centro escalado


def test_empty_catalog_is_a_clear_error() -> None:
    """Regresión: un catálogo vacío producía una excepción genérica."""
    project = Project(
        state=None,
        data=b"",
        geometry_revision="sha256:x",  # type: ignore[arg-type]
        source_revision="sha256:x",
        plan=None,
        objects=[],
    )
    with pytest.raises(AiError) as caught:
        build_context(ProposalRequest(provider="fake", description="x"), project, AiLimits())
    assert caught.value.kind == "empty_catalog" and caught.value.status == 409


@pytest.mark.parametrize(
    ("limits", "kw", "kind"),
    [
        ({"max_objects": 3}, {}, "too_many_objects"),
        ({"max_input_tokens": 500}, {}, "context_too_large"),
        ({"max_description_chars": 10}, {}, "description_too_long"),
        ({}, {"scope": "selection"}, "empty_scope"),
    ],
)
def test_scope_that_does_not_fit_is_not_truncated(
    job: Path, limits: dict[str, int], kw: dict[str, Any], kind: str
) -> None:
    fake = FakeProvider(brick("E-001"))
    with pytest.raises(AiError) as caught:
        ask(service(fake, **limits), job, **kw)
    assert caught.value.kind == kind and fake.calls == []


# --- Validación de la respuesta -----------------------------------------------------------------


def _bad(mutation: str):
    def make(ctx: dict[str, Any]) -> str:
        e1 = refs(ctx, "E-001")
        good = op(
            "op1",
            "material.assign",
            {"surface": "facade_exterior", "materialId": "brick_light"},
            targets(e1),
        )
        if mutation == "unknown_ref":
            return plan(op("op1", "object.move", {"dxM": 1, "dyM": 0}, targets(["o999"])))
        if mutation == "unknown_tool":
            return plan({**good, "tool": "facade.cut_openings"})
        if mutation == "bad_args":
            return plan({**good, "args": {**good["args"], "roughness": 2.0}})
        if mutation == "unknown_material":
            return plan({**good, "args": {**good["args"], "materialId": "marble"}})
        if mutation == "cycle":
            return plan(
                {**good, "dependsOn": ["op2"]},
                op("op2", "object.move", {"dxM": 1, "dyM": 0}, targets(e1), depends=["op1"]),
            )
        if mutation == "unknown_dependency":
            return plan({**good, "dependsOn": ["op9"]})
        if mutation == "unknown_pending":
            return plan({**good, "targets": targets(pending="q9")})
        if mutation == "url":
            return plan({**good, "reason": "Ver https://evil.example/x"})
        if mutation == "nan":
            return plan(good).replace('"roughness": null', '"roughness": NaN')
        if mutation == "unknown_capability":
            return plan(unsupported=[{"request": "x", "reason": "y", "capability": "teleport"}])
        return plan(good)[:-4]

    return make


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("unknown_ref", "unknown_object"),
        ("unknown_tool", "unknown_tool"),
        ("bad_args", "invalid_args"),
        ("unknown_material", "unknown_material"),
        ("cycle", "cycle"),
        ("unknown_dependency", "unknown_dependency"),
        ("unknown_pending", "unknown_pending"),
        ("url", "unsafe_text"),
        ("nan", "schema"),
        ("unknown_capability", "unknown_capability"),
        ("truncated", "schema"),
    ],
)
def test_invalid_responses_are_rejected_and_not_cached(job: Path, mutation: str, code: str) -> None:
    fake = FakeProvider(_bad(mutation))
    svc = service(fake)
    with pytest.raises(ProposalRejected) as caught:
        ask(svc, job)
    assert caught.value.issues[0]["code"] == code
    assert len(fake.calls) == 1 and not list((job / "ai").glob("cache/*.json"))


def test_declared_conflicts_block_the_affected_operations(job: Path) -> None:
    """Regresión: un conflicto declarado por la IA no bloqueaba las operaciones afectadas."""
    fake = FakeProvider(
        lambda ctx: plan(
            op(
                "op1",
                "material.assign",
                {"surface": "facade_exterior", "materialId": "brick_red"},
                targets(refs(ctx, "E-001")),
            ),
            op("op2", "object.move", {"dxM": 5, "dyM": 0}, targets(refs(ctx, "E-002"))),
            conflicts=[
                {
                    "description": "Pide rojo y blanco para E-001.",
                    "refs": refs(ctx, "E-001"),
                    "operations": ["op1"],
                }
            ],
        )
    )
    result = ask(service(fake), job)
    status = {o["id"]: o["status"] for o in result["operations"]}
    assert status == {"op1": "blocked", "op2": "ready"}


def test_dependencies_on_blocked_operations_block_too(job: Path) -> None:
    fake = FakeProvider(
        lambda ctx: plan(
            op(
                "op1",
                "building.set_height",
                {"mode": "absolute_m", "value": 20, "floorHeightM": None},
                targets(refs(ctx, "KIOSCO #1")),
            ),  # copia de bloque: incompatible
            op(
                "op2",
                "material.assign",
                {"surface": "roof", "materialId": "roof_gravel"},
                targets(refs(ctx, "KIOSCO #1")),
                depends=["op1"],
            ),
        )
    )
    result = ask(service(fake), job)
    by_id = {o["id"]: o for o in result["operations"]}
    assert by_id["op1"]["status"] == "incompatible"
    assert "copia de un bloque" in by_id["op1"]["incompatible"][0]["reason"]
    assert by_id["op2"]["status"] == "blocked"


def without_floor_height(job: Path) -> None:
    """GLB anterior a la altura de planta configurada (extras sin floor_height)."""
    editor = GlbEditor((job / "out" / "model.glb").read_bytes())
    for node in editor.doc["nodes"]:
        node.get("extras", {}).pop("floor_height", None)
    (job / "out" / "model.glb").write_bytes(editor.encode())


def test_floors_without_configured_height_need_user_input(job: Path) -> None:
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
    [item] = ask(service(fake), job)["operations"]
    assert item["status"] == "needs_input" and "altura de planta" in item["messages"][0]
    assert item["impact"]["heights"][0]["to"] is None  # no se inventa


def test_selectors_resolve_locally_with_exceptions(job: Path) -> None:
    fake = FakeProvider(
        lambda ctx: plan(
            op(
                "op1",
                "material.assign",
                {"surface": "roof", "materialId": "roof_gravel"},
                targets(kinds=["building"], **{"except": refs(ctx, "E-002")}),
            ),
        )
    )
    result = ask(service(fake), job, "Todas las cubiertas de grava salvo E-002")
    ids = set(result["operations"][0]["objectIds"])
    assert ids_of(job, "E-002")[0] not in ids
    assert set(ids_of(job, "E-001", "E-006", "KIOSCO #3")) <= ids


def test_ambiguous_reference_uses_explicit_rule_candidates(job: Path) -> None:
    result = ask(service(SimulatedProvider()), job, "Sube la torre central dos plantas")
    [pending] = result["pending"]
    assert pending["rule"]["by"] == "centrality" and len(pending["candidateObjectIds"]) == 1
    assert result["operations"][0]["status"] == "needs_resolution"
    north = ask(service(SimulatedProvider()), job, "Los del norte de ladrillo claro")
    # Regla explícita: edificios al norte del centro del conjunto (+Y del plano)
    candidates = set(north["pending"][0]["candidateObjectIds"])
    assert set(ids_of(job, "E-001", "E-002", "E-003")) <= candidates
    assert not candidates & set(ids_of(job, "E-007", "KIOSCO #1"))
    assert north["operations"][0]["objectIds"] == []  # nada se aplica sin confirmar


def test_unsupported_requests_name_the_missing_capability(job: Path) -> None:
    result = ask(
        service(SimulatedProvider()),
        job,
        "Pon ladrillo claro en todos salvo E-002 y abre huecos en la fachada sur",
    )
    assert result["unsupported"][0]["capability"] == "facade.openings_cut"
    assert result["operations"][0]["tool"] == "material.assign"


def test_aesthetic_requests_become_reviewable_assumptions(job: Path) -> None:
    result = ask(service(SimulatedProvider()), job, "Dale al conjunto un aspecto mediterráneo")
    assert result["assumptions"] and result["operations"][0]["basis"] == "assumption"


# --- Errores y límites sin llamadas adicionales ----------------------------------------------


@pytest.mark.parametrize(
    ("kind", "status"),
    [("truncated", 422), ("auth", 502), ("quota", 429), ("timeout", 504), ("network", 502)],
)
def test_errors_are_not_retried(job: Path, kind: str, status: int) -> None:
    fake = FakeProvider(brick("E-001"), error=ProviderError(kind, "falló", "arreglo"))
    with pytest.raises(AiError) as caught:
        ask(service(fake), job)
    assert (caught.value.kind, caught.value.status) == (kind, status) and len(fake.calls) == 1


def test_session_limit_and_concurrency(job: Path) -> None:
    fake = FakeProvider(brick("E-001"))
    svc = service(fake, session_requests=1)
    ask(svc, job, "a ladrillo")
    with pytest.raises(AiError) as caught:
        ask(svc, job, "b ladrillo")
    assert caught.value.kind == "session_limit" and len(fake.calls) == 1
    ask(svc, job, "b ladrillo", continueAfterLimit=True)
    gate = threading.Event()
    slow = FakeProvider(brick("E-001"), gate=gate)
    svc2 = service(slow)
    worker = threading.Thread(target=lambda: ask(svc2, job, "uno ladrillo"))
    worker.start()
    while not slow.calls:
        threading.Event().wait(0.01)
    with pytest.raises(AiError) as busy:
        ask(svc2, job, "dos ladrillo")
    gate.set()
    worker.join()
    assert busy.value.kind == "busy" and len(slow.calls) == 1


def test_piece_size_is_documented_per_material() -> None:
    """Regresión: tamaño de pieza ≠ mosaico de textura."""
    brick_light = next(p for p in DEFAULT_LIBRARY.presets if p.id == "brick_light")
    assert brick_light.tile_size_m == (2.0, 2.0)
    assert brick_light.piece_size_m == pytest.approx((0.25, 2.0 / 30))
    assert brick_light.tile_for_piece((0.3, 0.08)) == pytest.approx((2.4, 2.4))
