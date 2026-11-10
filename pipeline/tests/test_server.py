from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from extrude.server import CONFIG_HEADER, MAX_JOBS, create_server

CONFIGS = Path(__file__).parents[2] / "samples" / "config"
ORIGIN = "http://localhost:5173"


@pytest.fixture
def server(tmp_path: Path) -> Iterator[str]:
    srv = create_server("127.0.0.1", 0, jobs_dir=tmp_path / "jobs", max_mb=1)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def request(
    url: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    method: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)  # noqa: S310 (URL local)
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310 (URL local del test)
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def post_dxf(base: str, path: Path, **headers: str) -> tuple[int, dict[str, Any]]:
    status, _, body = request(
        f"{base}/api/convert?name={path.name}", path.read_bytes(), {"Origin": ORIGIN, **headers}
    )
    return status, json.loads(body)


def test_health_and_cors(server: str) -> None:
    status, headers, body = request(f"{server}/api/health", headers={"Origin": ORIGIN})
    assert status == 200
    assert json.loads(body)["ok"] is True
    assert headers["Access-Control-Allow-Origin"] == ORIGIN
    _, other, _ = request(f"{server}/api/health", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in other


def test_preflight_allows_config_header(server: str) -> None:
    status, headers, _ = request(
        f"{server}/api/convert", headers={"Origin": ORIGIN}, method="OPTIONS"
    )
    assert status == 204
    assert CONFIG_HEADER in headers["Access-Control-Allow-Headers"]


def test_dxf_without_config_generates_draft(server: str, samples_dir: Path) -> None:
    status, payload = post_dxf(server, samples_dir / "block_2d.dxf")
    assert status == 200, payload
    assert payload["config_generated"] is True
    assert payload["report"]["layers"]["EDIFICIOS"]["heights"] == {"text": 4}
    code, _, glb = request(server + payload["files"]["glb"])
    assert code == 200
    assert glb[:4] == b"glTF"
    code, _, config = request(server + payload["files"]["config"])
    assert yaml.safe_load(config)["layers"][0]["match"] == "AGUA"


def test_dxf_with_config_header(server: str, samples_dir: Path) -> None:
    config = (CONFIGS / "nested_blocks.yaml").read_bytes()
    status, payload = post_dxf(
        server,
        samples_dir / "nested_blocks.dxf",
        **{CONFIG_HEADER: base64.b64encode(config).decode()},
    )
    assert status == 200, payload
    assert payload["config_generated"] is False
    assert payload["report"]["layers"]["VEGETACION"]["instanced"] == {"ARBOL": 20}


@pytest.mark.parametrize(
    ("name", "body", "headers", "status", "fragment"),
    [
        ("plano.pdf", b"%PDF", {}, 415, ".dwg o .dxf"),
        ("plano.dxf", b"0\nEOF\n", {CONFIG_HEADER: "@@no-base64@@"}, 400, "Base64"),
        ("roto.dxf", b"basura que no es dxf", {}, 422, "'roto.dxf' no es un DXF"),
        ("plano.dwg", b"no es un dwg", {}, 422, "DWG"),
        ("plano.dxf", b"x" * (1_048_576 + 1), {}, 413, "límite"),
    ],
)
def test_bad_inputs_get_actionable_errors(
    server: str, name: str, body: bytes, headers: dict[str, str], status: int, fragment: str
) -> None:
    code, _, raw = request(f"{server}/api/convert?name={name}", body, headers)
    payload = json.loads(raw)
    assert code == status, payload
    assert fragment in payload["error"] + payload["fix"]


def test_invalid_config_is_reported(server: str, samples_dir: Path) -> None:
    bad = base64.b64encode(b"layers:\n  - match: A\n    kind: castle\n").decode()
    status, payload = post_dxf(server, samples_dir / "block_2d.dxf", **{CONFIG_HEADER: bad})
    assert status == 422
    assert "kind" in payload["error"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/jobs/../../etc/passwd/x",
        "/api/jobs/zzzz/model.glb",
        "/api/jobs/" + "a" * 32 + "/secret.txt",
    ],
)
def test_job_paths_are_validated(server: str, path: str) -> None:
    code, _, _ = request(server + path)
    assert code == 404


def test_old_jobs_are_pruned(tmp_path: Path, samples_dir: Path) -> None:
    from extrude.server import JobStore

    store = JobStore(tmp_path / "jobs")
    for _ in range(MAX_JOBS + 3):
        store.create()
    assert len(list((tmp_path / "jobs").iterdir())) == MAX_JOBS
