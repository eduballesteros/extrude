"""Modo multiusuario (`extrude serve --shared`): sesión, aislamiento entre usuarios, roles,
control de concurrencia y límites."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from ai_fixtures import write_model
from test_server import ORIGIN, request

from extrude.access import AccessControl, Accounts, Limits, SharedMode
from extrude.ai_planner import AiLimits, AiService, model_objects
from extrude.ai_providers import SimulatedProvider
from extrude.errors import ExtrudeError
from extrude.materials_service import BASE_GLB
from extrude.server import create_server

PASSWORDS = {"ana": "contraseña-larga-ana", "bea": "contraseña-larga-bea"}


@pytest.fixture(scope="module")
def base_glb(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    return (write_model(tmp_path_factory.mktemp("shared") / "out") / BASE_GLB).read_bytes()


@pytest.fixture
def shared(tmp_path: Path) -> SharedMode:
    accounts = Accounts(tmp_path / "users.json")
    for name, password in PASSWORDS.items():
        accounts.add(name, password)
    accounts.add("carla", "contraseña-larga-carla")
    return SharedMode(
        accounts,
        AccessControl(tmp_path / "jobs"),
        limits=Limits(max_failed_logins=3, max_jobs_per_user=2, max_ai_per_hour=2),
    )


@pytest.fixture
def server(tmp_path: Path, shared: SharedMode) -> Iterator[str]:
    (tmp_path / "jobs").mkdir(exist_ok=True)
    ai = AiService({"simulated": SimulatedProvider()}, AiLimits(), prices={})
    srv = create_server("127.0.0.1", 0, jobs_dir=tmp_path / "jobs", max_mb=5, ai=ai, shared=shared)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def call(
    base: str, path: str, token: str | None = None, body: Any = None, raw: bytes | None = None
) -> tuple[int, Any]:
    headers = {"Origin": ORIGIN}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    code, _, out = request(f"{base}{path}", data, headers)
    try:
        return code, json.loads(out)
    except ValueError:
        return code, out


def login(base: str, user: str) -> str:
    code, data = call(base, "/api/auth/login", body={"user": user, "password": PASSWORDS[user]})
    assert code == 200, data
    token: str = data["token"]
    return token


def test_passwords_are_hashed_and_names_validated(tmp_path: Path) -> None:
    accounts = Accounts(tmp_path / "u.json")
    accounts.add("ana", "una-frase-bastante-larga")
    stored = (tmp_path / "u.json").read_text(encoding="utf-8")
    assert "una-frase" not in stored and "salt" in stored
    assert accounts.verify("ana", "una-frase-bastante-larga")
    assert not accounts.verify("ana", "otra-frase-bastante-larga")
    assert not accounts.verify("nadie", "una-frase-bastante-larga")
    with pytest.raises(ExtrudeError):
        accounts.add("Ana Mayúsculas", "una-frase-bastante-larga")
    with pytest.raises(ExtrudeError):
        accounts.add("eva", "corta")


def test_everything_but_health_and_login_needs_a_session(server: str) -> None:
    assert call(server, "/api/health")[0] == 200
    assert call(server, "/api/auth/me")[1] == {"mode": "shared", "user": None}
    code, data = call(server, "/api/library")
    assert code == 401 and data["kind"] == "auth_required"
    assert call(server, "/api/library", "token-inventado")[0] == 401
    code, _ = call(server, "/api/auth/login", body={"user": "ana", "password": "mala-contraseña"})
    assert code == 401
    token = login(server, "ana")
    assert call(server, "/api/auth/me", token)[1]["user"] == "ana"
    assert call(server, "/api/library", token)[0] == 200
    assert call(server, "/api/auth/logout", token, body={})[0] == 200
    assert call(server, "/api/library", token)[0] == 401  # sesión cerrada


def test_failed_logins_lock_the_account_for_a_while(server: str) -> None:
    for _ in range(3):
        call(server, "/api/auth/login", body={"user": "bea", "password": "no-es-esta-clave"})
    code, data = call(server, "/api/auth/login", body={"user": "bea", "password": PASSWORDS["bea"]})
    assert code == 429 and "intentos" in data["error"]


def test_projects_are_isolated_and_shared_by_role(server: str, base_glb: bytes) -> None:
    ana, bea = login(server, "ana"), login(server, "bea")
    code, made = call(server, "/api/models", ana, raw=base_glb)
    assert code == 201
    job = made["job"]
    assert call(server, f"/api/jobs/{job}/project", ana)[0] == 200
    # Bea no lo ve: 404 como si no existiera (ni el GLB, ni el estado, ni el historial)
    for path in ("project", "model.glb", "objects.json", "access"):
        assert call(server, f"/api/jobs/{job}/{path}", bea)[0] == 404, path
    code, _ = call(server, f"/api/jobs/{job}/project/undo?expect=rev-000", bea, body={})
    assert code == 404
    # Solo la dueña comparte; Bea lectora puede ver pero no cambiar
    assert (
        call(server, f"/api/jobs/{job}/access", bea, body={"user": "bea", "role": "editor"})[0]
        == 404
    )
    code, access = call(
        server, f"/api/jobs/{job}/access", ana, body={"user": "bea", "role": "viewer"}
    )
    assert code == 200 and access["members"] == {"bea": "viewer"}
    assert call(server, f"/api/jobs/{job}/project", bea)[0] == 200
    code, _ = call(
        server,
        f"/api/jobs/{job}/ai/context",
        bea,
        body={"provider": "simulated", "description": "x"},
    )
    assert code == 403
    call(server, f"/api/jobs/{job}/access", ana, body={"user": "bea", "role": "editor"})
    code, _ = call(
        server,
        f"/api/jobs/{job}/ai/context",
        bea,
        body={
            "provider": "simulated",
            "description": "Sube E-001 una planta",
            "baseRevision": "rev-000",
        },
    )
    assert code == 200
    call(server, f"/api/jobs/{job}/access", ana, body={"user": "bea", "role": None})
    assert call(server, f"/api/jobs/{job}/project", bea)[0] == 404  # acceso retirado


def test_concurrent_confirmations_need_and_respect_the_base_revision(
    server: str, base_glb: bytes
) -> None:
    ana, bea = login(server, "ana"), login(server, "bea")
    job = call(server, "/api/models", ana, raw=base_glb)[1]["job"]
    call(server, f"/api/jobs/{job}/access", ana, body={"user": "bea", "role": "editor"})
    project = call(server, f"/api/jobs/{job}/project", ana)[1]
    first = next(o.id for o in model_objects(base_glb, "r") if o.label == "E-001")
    plan = {
        "schemaVersion": 1,
        "projectId": "visor",
        "geometryRevision": project["geometryRevision"],
        "materialRevision": "mat-000",
        "catalogVersion": "library-1",
        "assignments": [
            {
                "objectIds": [first],
                "surface": "facade_exterior",
                "materialId": "brick_red",
                "materialVersion": "1",
                "origin": "manual",
            }
        ],
    }
    url = f"/api/jobs/{job}/materials"
    code, _ = call(server, url, ana, body=plan)
    assert code == 428  # sin revisión base no se confirma en un servidor compartido
    assert call(server, f"{url}?base=rev-000", ana, body=plan)[0] == 200
    # Bea trabajaba sobre rev-000: su confirmación llega tarde → 409 y nada cambia
    code, stale = call(
        server, f"{url}?base=rev-000", bea, body={**plan, "materialRevision": "mat-001"}
    )
    assert code == 409 and stale["revision"] == "rev-001"
    assert call(server, f"/api/jobs/{job}/project", ana)[1]["revision"] == "rev-001"
    assert call(server, f"/api/jobs/{job}/project/undo", bea, body={})[0] == 428
    assert call(server, f"/api/jobs/{job}/project/undo?expect=rev-000", bea, body={})[0] == 409


def test_per_user_limits(server: str, base_glb: bytes) -> None:
    carla_pw = "contraseña-larga-carla"
    carla = call(server, "/api/auth/login", body={"user": "carla", "password": carla_pw})[1][
        "token"
    ]
    jobs = [call(server, "/api/models", carla, raw=base_glb) for _ in range(3)]
    assert [code for code, _ in jobs] == [201, 201, 429]
    job = jobs[0][1]["job"]
    ask = {
        "provider": "simulated",
        "description": "Sube E-001 una planta",
        "consent": True,
        "baseRevision": "rev-000",
    }
    codes = [
        call(
            server,
            f"/api/jobs/{job}/ai/propose",
            carla,
            body={**ask, "description": f"Sube E-00{i} una planta"},
        )[0]
        for i in (1, 2, 3)
    ]
    assert codes[:2] == [200, 200] and codes[2] == 429


def test_local_mode_is_unchanged(tmp_path: Path, base_glb: bytes) -> None:
    srv = create_server("127.0.0.1", 0, jobs_dir=tmp_path / "local", max_mb=5)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert call(base, "/api/auth/me")[1] == {"mode": "local", "user": None}
        code, made = call(base, "/api/models", raw=base_glb)
        assert code == 201
        assert call(base, f"/api/jobs/{made['job']}/project")[0] == 200
    finally:
        srv.shutdown()
        srv.server_close()


def test_shared_mode_requires_accounts(tmp_path: Path) -> None:
    empty = SharedMode(Accounts(tmp_path / "none.json"), AccessControl(tmp_path))
    with pytest.raises(ExtrudeError):
        create_server("127.0.0.1", 0, jobs_dir=tmp_path, shared=empty)


def test_other_users_cannot_prune_owned_projects(
    server: str, shared: SharedMode, base_glb: bytes
) -> None:
    shared.limits.max_jobs_per_user = 50
    ana, bea = login(server, "ana"), login(server, "bea")
    made = call(server, "/api/models", ana, raw=base_glb)[1]
    path = f"/api/jobs/{made['job']}/project"
    for _ in range(12):
        assert call(server, "/api/models", bea, raw=base_glb)[0] == 201
    assert call(server, path, ana)[0] == 200
    assert call(server, path, bea)[0] == 404
    assert shared.access.owned_by("ana") == 1
    assert shared.access.owned_by("bea") == 12
