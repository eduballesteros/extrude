"""Materialidad en extrude serve: biblioteca, GLB subido, previsualizar y confirmar (Fase 2)."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
from test_server import ORIGIN, post_dxf, request, server  # noqa: F401 (fixture)

from extrude.material_library import LIBRARY_VERSION

CONFIGS = Path(__file__).parents[2] / "samples" / "config"


def converted(base: str, samples_dir: Path) -> dict[str, Any]:
    config = (CONFIGS / "block_2d.yaml").read_text(encoding="utf-8")

    status, payload = post_dxf(
        base,
        samples_dir / "block_2d.dxf",
        **{"X-Extrude-Config": base64.b64encode(config.encode()).decode()},
    )
    assert status == 200, payload
    return payload


def catalog(base: str, payload: dict[str, Any]) -> dict[str, Any]:
    code, _, body = request(f"{base}/api/jobs/{payload['job']}/objects.json")
    assert code == 200
    data: dict[str, Any] = json.loads(body)
    return data


def plan(revision: str, ids: list[str], surface: str = "facade_exterior") -> bytes:
    return json.dumps(
        {
            "schemaVersion": 1,
            "projectId": "web",
            "geometryRevision": revision,
            "materialRevision": "mat-999",  # el servidor pone la suya al confirmar
            "catalogVersion": LIBRARY_VERSION,
            "assignments": [
                {
                    "objectIds": ids,
                    "surface": surface,
                    "materialId": "brick_light",
                    "materialVersion": "1",
                    "origin": "manual",
                }
            ],
        }
    ).encode()


def post_plan(base: str, job: str, body: bytes, preview: bool) -> tuple[int, dict[str, Any]]:
    query = "?preview=1" if preview else ""
    code, _, data = request(
        f"{base}/api/jobs/{job}/materials{query}",
        body,
        {"Origin": ORIGIN, "Content-Type": "application/json"},
    )
    return code, json.loads(data)


def test_library_and_textures(server: str) -> None:  # noqa: F811
    code, headers, body = request(f"{server}/api/library", headers={"Origin": ORIGIN})
    assert code == 200 and headers["Access-Control-Allow-Origin"] == ORIGIN
    library = json.loads(body)
    assert library["version"] == LIBRARY_VERSION
    brick = next(m for m in library["materials"] if m["id"] == "brick_light")
    code, headers, png = request(server + brick["texture"])
    assert code == 200 and headers["Content-Type"] == "image/png"
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert request(f"{server}/api/library/nope.png")[0] == 404
    assert request(f"{server}/api/library/..%2Fx.png")[0] == 404


def test_preview_then_confirm_keeps_confirmed_state_separate(
    server: str,  # noqa: F811
    samples_dir: Path,
) -> None:
    payload = converted(server, samples_dir)
    job = payload["job"]
    objects = catalog(server, payload)
    ids = [o["id"] for o in objects["objects"] if o["layer"] == "EDIFICIOS"]
    body = plan(objects["geometryRevision"], ids)

    code, preview = post_plan(server, job, body, preview=True)
    assert code == 200, preview
    assert preview["url"].endswith("/model.preview.glb")
    assert preview["plan"] is None
    # Previsualizar no crea estado confirmado (RF-08)
    assert request(f"{server}/api/jobs/{job}/materials.json")[0] == 404
    code, _, glb = request(server + preview["url"])
    assert code == 200 and glb[:4] == b"glTF"

    code, first = post_plan(server, job, body, preview=False)
    assert code == 200, first
    assert first["materialRevision"] == "mat-001"
    code, _, saved = request(server + first["planUrl"])
    assert code == 200 and json.loads(saved)["materialRevision"] == "mat-001"
    code, second = post_plan(server, job, body, preview=False)
    assert second["materialRevision"] == "mat-002"  # cada confirmación es una revisión nueva


def test_invalid_plan_reports_issues_and_writes_nothing(
    server: str,  # noqa: F811
    samples_dir: Path,
) -> None:
    payload = converted(server, samples_dir)
    job = payload["job"]
    objects = catalog(server, payload)
    road = [o["id"] for o in objects["objects"] if o["layer"] == "VIALES"]
    code, result = post_plan(
        server, job, plan(objects["geometryRevision"], road, "facade_courtyard"), preview=False
    )
    assert code == 422
    assert result["issues"][0]["code"] == "missing_surface"
    assert request(f"{server}/api/jobs/{job}/materials.json")[0] == 404
    assert request(f"{server}/api/jobs/{job}/model.materialized.glb")[0] == 404
    code, result = post_plan(server, job, b"{", preview=True)
    assert code == 422 and "esquema" in result["error"]


def test_uploaded_base_glb_can_be_materialized(
    server: str,  # noqa: F811
    samples_dir: Path,
) -> None:
    payload = converted(server, samples_dir)
    _, _, glb = request(server + payload["files"]["glb"])
    objects = catalog(server, payload)
    code, _, body = request(f"{server}/api/models", glb, {"Origin": ORIGIN})
    created = json.loads(body)
    assert code == 201, created
    assert created["geometryRevision"] == objects["geometryRevision"]
    ids = [o["id"] for o in objects["objects"] if o["layer"] == "EDIFICIOS"]
    code, result = post_plan(
        server, created["job"], plan(objects["geometryRevision"], ids), preview=True
    )
    assert code == 200, result

    # Un GLB materializado no es una base válida
    _, _, materialized = request(server + result["url"])
    code, _, body = request(f"{server}/api/models", materialized, {"Origin": ORIGIN})
    assert code == 422 and "materializado" in json.loads(body)["error"]


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Origin": "https://evil.example", "Content-Type": "application/json"}, 403),
        ({"Origin": ORIGIN, "Content-Type": "text/plain"}, 415),
    ],
)
def test_browser_rules_for_plans(
    server: str,  # noqa: F811
    headers: dict[str, str],
    status: int,
) -> None:
    code, _, _ = request(f"{server}/api/jobs/{'a' * 32}/materials", b"{}", headers)
    assert code == status


def test_unknown_job_is_not_found(server: str) -> None:  # noqa: F811
    code, _, _ = request(
        f"{server}/api/jobs/{'0' * 32}/materials",
        b"{}",
        {"Origin": ORIGIN, "Content-Type": "application/json"},
    )
    assert code == 404
