"""POST /api/convert?async=1 + GET /api/jobs/<id>/status: progreso por fases."""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from extrude.config import load_config
from extrude.convert import ConvertOptions, convert
from extrude.server import CONFIG_HEADER, PHASE_START, create_server, phase_fraction

CONFIGS = Path(__file__).parents[2] / "samples" / "config"
ORIGIN = "http://localhost:5173"
TIMEOUT_S = 60


@pytest.fixture
def server(tmp_path: Path) -> Iterator[str]:
    srv = create_server("127.0.0.1", 0, jobs_dir=tmp_path / "jobs", max_mb=1)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def call(url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> Any:
    sent = {"Origin": ORIGIN, "Content-Type": "application/octet-stream", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=sent)  # noqa: S310 (URL local)
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310 (URL local del test)
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def post_async(base: str, path: Path, query: str = "", config: Path | None = None) -> Any:
    headers = {CONFIG_HEADER: base64.b64encode(config.read_bytes()).decode()} if config else {}
    return call(f"{base}/api/convert?name={path.name}&async=1{query}", path.read_bytes(), headers)


def poll(base: str, status_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Consulta el estado hasta que termina; devuelve el final y todos los vistos."""
    seen: list[dict[str, Any]] = []
    deadline = time.monotonic() + TIMEOUT_S
    while time.monotonic() < deadline:
        code, status = call(base + status_url)
        assert code == 200, status
        seen.append(status)
        if status["state"] in ("done", "failed"):
            return status, seen
        time.sleep(0.005)
    raise AssertionError(f"el trabajo no terminó: {seen[-1]}")


def test_async_malformed_yaml_is_reported_as_input_error(server: str, samples_dir: Path) -> None:
    code, accepted = call(
        f"{server}/api/convert?name=p.dxf&async=1",
        (samples_dir / "block_2d.dxf").read_bytes(),
        {CONFIG_HEADER: base64.b64encode(b"layers: [").decode()},
    )
    assert code == 202
    final, _ = poll(server, accepted["status"])
    assert final["state"] == "failed"
    assert final["http_status"] == 422
    assert "YAML" in final["error"]
    assert "line" in final["error"] and "column" in final["error"]


def test_async_job_reports_phases_and_result(server: str, samples_dir: Path) -> None:
    code, accepted = post_async(
        server, samples_dir / "block_2d.dxf", config=CONFIGS / "block_2d.yaml"
    )
    assert code == 202
    assert accepted["state"] == "pending"
    assert accepted["status"] == f"/api/jobs/{accepted['job']}/status"

    final, seen = poll(server, accepted["status"])
    assert final["state"] == "done"
    assert final["phase"] == "done"
    assert final["fraction"] == 1.0
    result = final["result"]
    assert result["job"] == accepted["job"]
    assert result["config_generated"] is False
    assert result["files"]["glb"] == f"/api/jobs/{accepted['job']}/model.glb"
    fractions = [s["fraction"] for s in seen]
    assert fractions == sorted(fractions)  # el progreso nunca retrocede
    assert {s["state"] for s in seen} <= {"pending", "running", "done"}
    code, _ = call(server + result["files"]["report_json"])
    assert code == 200


def test_async_job_without_config_drafts_first(server: str, samples_dir: Path) -> None:
    _, accepted = post_async(server, samples_dir / "block_2d.dxf")
    final, _ = poll(server, accepted["status"])
    assert final["state"] == "done"
    assert final["result"]["config_generated"] is True


def test_async_failure_is_reported_in_status(server: str, tmp_path: Path) -> None:
    broken = tmp_path / "roto.dxf"
    broken.write_bytes(b"basura que no es dxf")
    code, accepted = post_async(server, broken)
    assert code == 202
    final, _ = poll(server, accepted["status"])
    assert final["state"] == "failed"
    assert final["http_status"] == 422
    assert "'roto.dxf' no es un DXF" in final["error"]
    assert final["fix"]


@pytest.mark.parametrize(
    ("name", "query", "status"),
    [
        ("plano.pdf", "", 415),
        ("block_2d.dxf", "&tiles=-1", 400),
        ("block_2d.dxf", "&compress=zip", 400),
    ],
)
def test_async_rejects_bad_requests_up_front(
    server: str, samples_dir: Path, tmp_path: Path, name: str, query: str, status: int
) -> None:
    path = tmp_path / name
    path.write_bytes((samples_dir / "block_2d.dxf").read_bytes())
    code, payload = post_async(server, path, query)
    assert code == status, payload
    assert "job" not in payload


def test_unknown_job_has_no_status(server: str) -> None:
    assert call(f"{server}/api/jobs/{'a' * 32}/status")[0] == 404
    assert call(f"{server}/api/jobs/not-a-job/status")[0] == 404


def test_phase_fraction_is_monotonic() -> None:
    order = list(PHASE_START)
    for drafting in (False, True):
        values = [phase_fraction(p, drafting=drafting) for p in order]
        assert values == sorted(values)
        assert values[-1] == 1.0
    assert phase_fraction("draft_config", drafting=True) == 0.0


def test_convert_reports_each_phase_in_order(samples_dir: Path, tmp_path: Path) -> None:
    phases: list[str] = []
    config = load_config(CONFIGS / "block_2d.yaml")
    options = ConvertOptions(config, tmp_path, on_phase=phases.append)
    result = convert(samples_dir / "block_2d.dxf", options)
    assert phases == ["dwg_to_dxf", "read", "build", "instancing", "export"]
    assert list(result.timings) == phases
