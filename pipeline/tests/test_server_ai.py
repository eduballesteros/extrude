"""Edición con IA por HTTP: herramientas, contexto, propuesta, aplicar, deshacer y reabrir."""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from ai_fixtures import write_model
from test_server import ORIGIN, request

from extrude.ai_planner import AiLimits, AiService
from extrude.ai_providers import AnthropicProvider, Completion, SimulatedProvider
from extrude.material_library import LIBRARY_VERSION
from extrude.materials_service import BASE_GLB
from extrude.server import create_server

JSON = {"Origin": ORIGIN, "Content-Type": "application/json"}


@dataclass
class Counting(SimulatedProvider):
    calls: int = 0

    def complete(self, **kwargs: Any) -> Completion:  # type: ignore[override]
        self.calls += 1
        return super().complete(**kwargs)


def start(jobs: Path, ai: AiService) -> Iterator[str]:
    srv = create_server("127.0.0.1", 0, jobs_dir=jobs, max_mb=5, ai=ai)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def sim() -> Counting:
    return Counting()


@pytest.fixture
def server(tmp_path: Path, sim: Counting) -> Iterator[str]:
    yield from start(tmp_path / "jobs", AiService({"simulated": sim}, AiLimits(), prices={}))


@pytest.fixture(scope="module")
def base_glb(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    return (write_model(tmp_path_factory.mktemp("srv-ai") / "out") / BASE_GLB).read_bytes()


def upload(base: str, glb: bytes) -> str:
    code, _, body = request(f"{base}/api/models", glb, {"Origin": ORIGIN})
    assert code == 201, body
    job: str = json.loads(body)["job"]
    return job


def post(
    base: str, path: str, payload: dict[str, Any], headers: dict[str, str] | None = None
) -> tuple[int, dict[str, Any]]:
    code, _, body = request(f"{base}{path}", json.dumps(payload).encode(), headers or JSON)
    return code, json.loads(body)


def get(base: str, path: str) -> dict[str, Any]:
    code, _, body = request(f"{base}{path}", headers={"Origin": ORIGIN})
    assert code == 200, body
    data: dict[str, Any] = json.loads(body)
    return data


TEXT = "Sube E-001 dos plantas, pon ladrillo rojo en todos salvo E-002 y abre huecos en la fachada"


def test_tools_and_providers_are_listed(server: str) -> None:
    tools = get(server, "/api/tools")
    names = {t["name"] for t in tools["tools"]}
    assert {"material.assign", "building.set_height", "object.move", "view.create"} <= names
    assert any(p["id"] == "facade.openings_cut" for p in tools["planned"])
    assert get(server, "/api/ai/providers")["providers"][0]["id"] == "simulated"


def test_full_flow_preview_confirm_undo_and_reopen(
    tmp_path: Path, server: str, sim: Counting, base_glb: bytes
) -> None:
    job = upload(server, base_glb)
    project = get(server, f"/api/jobs/{job}/project")
    assert project["revision"] == "rev-000" and not project["canUndo"]
    ask = {"provider": "simulated", "description": TEXT, "baseRevision": "rev-000"}

    code, preview = post(server, f"/api/jobs/{job}/ai/context", ask)
    assert code == 200 and sim.calls == 0 and len(preview["contextHash"]) == 64
    code, refused = post(server, f"/api/jobs/{job}/ai/propose", ask)
    assert code == 403 and refused["kind"] == "consent" and sim.calls == 0
    authorized = {**ask, "consent": True, "consentHash": preview["contextHash"]}
    code, changed = post(
        server, f"/api/jobs/{job}/ai/propose", {**authorized, "description": "otra"}
    )
    assert code == 409 and changed["kind"] == "consent_changed" and sim.calls == 0

    code, plan = post(server, f"/api/jobs/{job}/ai/propose", authorized)
    assert code == 200, plan
    assert sim.calls == 1
    assert plan["unsupported"][0]["capability"] == "facade.openings_cut"
    ops = [o["id"] for o in plan["operations"] if o["status"] == "ready"]
    body = {
        "planId": plan["planId"],
        "baseRevision": "rev-000",
        "operations": ops,
        "description": TEXT,
    }

    code, shown = post(server, f"/api/jobs/{job}/edit/apply?preview=1", body)
    assert code == 200, shown
    code, _, glb = request(server + shown["url"])
    assert code == 200 and glb[:4] == b"glTF"
    assert get(server, f"/api/jobs/{job}/project")["revision"] == "rev-000"

    code, done = post(server, f"/api/jobs/{job}/edit/apply", body)
    assert code == 200, done
    assert done["project"]["revision"] == "rev-001" and done["project"]["canUndo"]
    code, _, model = request(server + done["project"]["modelUrl"])
    assert code == 200 and b"brick_red" in model  # el GLB servido es el editado y materializado
    saved = json.loads(request(server + done["project"]["files"]["plan"])[2])
    assert saved["description"] == TEXT and saved["ai"]["provider"] == "simulated"

    code, stale = post(server, f"/api/jobs/{job}/edit/apply", body)
    assert code == 409 and stale["kind"] == "stale_plan"

    code, undone = post(server, f"/api/jobs/{job}/project/undo", {})
    assert code == 200 and undone["revision"] == "rev-000" and undone["canRedo"]
    code, redone = post(server, f"/api/jobs/{job}/project/redo", {})
    assert redone["revision"] == "rev-001"
    assert sim.calls == 1  # aplicar, confirmar, deshacer y rehacer no consultan

    # Reabrir: otro servidor sobre la misma carpeta, sin proveedores de IA
    restarted = start(tmp_path / "jobs", AiService({}, AiLimits(), prices={}))
    base2 = next(restarted)
    try:
        again = get(base2, f"/api/jobs/{job}/project")
        assert again["revision"] == "rev-001"
        code, _, model2 = request(base2 + again["modelUrl"])
        assert code == 200 and model2 == model
    finally:
        next(restarted, None)


def test_manual_form_works_without_any_provider(
    tmp_path: Path, base_glb: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ai = AiService({"anthropic": AnthropicProvider()}, AiLimits(), prices={})
    servers = start(tmp_path / "jobs", ai)
    base = next(servers)
    try:
        job = upload(base, base_glb)
        code, error = post(
            base,
            f"/api/jobs/{job}/ai/propose",
            {"provider": "anthropic", "description": "x", "consent": True},
        )
        assert code == 503 and error["kind"] == "unavailable"
        revision = get(base, f"/api/jobs/{job}/project")["geometryRevision"]
        plan = {
            "schemaVersion": 1,
            "projectId": "visor",
            "geometryRevision": revision,
            "materialRevision": "mat-000",
            "catalogVersion": LIBRARY_VERSION,
            "assignments": [],
        }
        code, result = post(base, f"/api/jobs/{job}/materials", plan)
        assert code == 200, result
        assert get(base, f"/api/jobs/{job}/project")["revision"] == "rev-001"
    finally:
        next(servers, None)


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Origin": "https://evil.example", "Content-Type": "application/json"}, 403),
        ({"Origin": ORIGIN, "Content-Type": "text/plain"}, 415),
    ],
)
def test_browser_rules_apply_to_edit_routes(
    server: str, sim: Counting, base_glb: bytes, headers: dict[str, str], status: int
) -> None:
    job = upload(server, base_glb)
    for path in ("ai/propose", "edit/apply", "project/undo"):
        code, _ = post(
            server,
            f"/api/jobs/{job}/{path}",
            {"provider": "simulated", "description": "x", "consent": True},
            headers,
        )
        assert code == status
    assert sim.calls == 0


def test_cancelled_wait_keeps_the_server_healthy(tmp_path: Path, base_glb: bytes) -> None:
    slow = Counting(delay_s=1.0)
    servers = start(tmp_path / "jobs", AiService({"simulated": slow}, AiLimits(), prices={}))
    base = next(servers)
    try:
        job = upload(base, base_glb)
        payload = {"provider": "simulated", "description": TEXT, "consent": True}
        with pytest.raises((TimeoutError, socket.timeout, urllib.error.URLError)):
            req = urllib.request.Request(  # noqa: S310 (URL local)
                f"{base}/api/jobs/{job}/ai/propose", json.dumps(payload).encode(), JSON
            )
            urllib.request.urlopen(req, timeout=0.2)  # noqa: S310 (el visor cancela la espera)
        # El proveedor termina igualmente: la respuesta tardía no se aplica, pero queda en caché
        for _ in range(50):
            code, result = post(base, f"/api/jobs/{job}/ai/propose", payload)
            if code != 409:
                break
            threading.Event().wait(0.1)
        assert code == 200 and result["cached"] is True and slow.calls == 1
    finally:
        next(servers, None)
