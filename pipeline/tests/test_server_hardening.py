"""Robustez del servidor frente a lo encontrado en la auditoría: hilo de conversión que no
muere, puerto exclusivo, Host exigido en todas las interfaces, límites de acceso por cliente,
estados sin el informe en memoria y diagnóstico medido de lo adaptado al terreno."""

from __future__ import annotations

import contextlib
import io
import json
import socket
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import ezdxf
import pytest
from test_server import ORIGIN, request

import extrude.server as srv
from extrude.access import AccessControl, Accounts, Limits, RateLimiter, SharedMode
from extrude.build import build_scene
from extrude.config import parse_config
from extrude.dxf_reader import read_dxf
from extrude.errors import ExtrudeError
from extrude.geodesy import to_wgs84, utm_zone
from extrude.server import create_server

SAMPLES = Path(__file__).parents[2] / "samples"
DXF = (SAMPLES / "block_2d.dxf").read_bytes()
TIMEOUT_S = 30


def start(tmp: Path, **kwargs: Any) -> tuple[Any, str]:
    server = create_server("127.0.0.1", 0, jobs_dir=tmp / "jobs", max_mb=5, **kwargs)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def call(url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> Any:
    code, _, body = request(url, data, {"Origin": ORIGIN, **(headers or {})})
    try:
        return code, json.loads(body)
    except ValueError:
        return code, body


def convert(base: str) -> dict[str, Any]:
    code, accepted = call(f"{base}/api/convert?name=p.dxf&async=1", DXF)
    assert code == 202, accepted
    return dict(accepted)


def final_state(base: str, status: str) -> dict[str, Any]:
    deadline = time.monotonic() + TIMEOUT_S
    while time.monotonic() < deadline:
        _, state = call(base + status)
        if state["state"] in ("done", "failed"):
            return dict(state)
        time.sleep(0.05)
    raise AssertionError(f"el trabajo no terminó: {state}")


@pytest.fixture
def server(tmp_path: Path) -> Iterator[str]:
    instance, base = start(tmp_path)
    yield base
    instance.shutdown()
    instance.server_close()


# --- A1: el hilo de conversión no muere ---------------------------------------------------------


def test_worker_survives_a_failure_while_reporting_an_error(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Una excepción inesperada con texto que la consola no codifica, una salida estricta y un
    fallo al publicar el error: el trabajo acaba en «failed» y el siguiente se convierte."""
    real_failed = srv._failed
    calls = {"n": 0}

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("capa «Ω→漢» ilegible")

    def failed_once(*args: Any, **kwargs: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("salida cerrada")  # el propio informe del error falla
        real_failed(*args, **kwargs)

    strict = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", strict)
    monkeypatch.setattr(sys, "stderr", strict)
    monkeypatch.setattr(srv, "finish_job", explode)
    monkeypatch.setattr(srv, "_failed", failed_once)
    first = convert(server)
    broken = final_state(server, first["status"])
    assert broken["state"] == "failed" and broken["http_status"] == 500

    monkeypatch.undo()
    second = convert(server)
    assert final_state(server, second["status"])["state"] == "done"


def test_log_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    strict = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stderr", strict)
    srv.log("Ω → 漢")  # no lanza: se escapa
    strict.flush()
    assert b"\\u03a9" in strict.buffer.getvalue()  # type: ignore[attr-defined]
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "stdout", None)
    srv.log("sin consola")  # pythonw: no hay salida


# --- A2: un solo servidor por puerto ------------------------------------------------------------


def test_second_server_on_the_same_port_fails(server: str, tmp_path: Path) -> None:
    port = int(server.rsplit(":", 1)[1])
    with pytest.raises(ExtrudeError) as caught:
        create_server("127.0.0.1", port, jobs_dir=tmp_path / "otro", max_mb=5)
    assert "otro `extrude serve`" in caught.value.fix


# --- M1: Host exigido en todas las interfaces ----------------------------------------------------


def test_allowed_hosts_are_enforced_on_all_interfaces(tmp_path: Path) -> None:
    open_server = create_server("0.0.0.0", 0, jobs_dir=tmp_path / "a", max_mb=5)  # noqa: S104
    threading.Thread(target=open_server.serve_forever, daemon=True).start()
    closed = create_server(
        "0.0.0.0",  # noqa: S104
        0,
        jobs_dir=tmp_path / "b",
        max_mb=5,
        allowed_hosts=("localhost", "127.0.0.1"),
    )
    threading.Thread(target=closed.serve_forever, daemon=True).start()
    try:
        for instance, expected in ((open_server, 200), (closed, 421)):
            base = f"http://127.0.0.1:{instance.server_address[1]}"
            code, _ = call(f"{base}/api/projects", headers={"Host": "atacante.example:8080"})
            assert code == expected
        base = f"http://127.0.0.1:{closed.server_address[1]}"
        assert call(f"{base}/api/projects", headers={"Host": "localhost:8080"})[0] == 200
        assert call(f"{base}/api/health")[0] == 200  # Host: 127.0.0.1:PUERTO
    finally:
        for instance in (open_server, closed):
            instance.shutdown()
            instance.server_close()


# --- M2: límites de acceso ------------------------------------------------------------------------


def _shared(tmp_path: Path, **limits: Any) -> SharedMode:
    accounts = Accounts(tmp_path / "users.json")
    accounts.add("ana", "contraseña-larga-ana")
    (tmp_path / "jobs").mkdir(exist_ok=True)
    return SharedMode(accounts, AccessControl(tmp_path / "jobs"), limits=Limits(**limits))


def test_failed_logins_from_elsewhere_do_not_lock_out_the_owner(tmp_path: Path) -> None:
    mode = _shared(tmp_path, max_failed_logins=3, max_failed_per_client=10)
    for _ in range(3):
        assert mode.login("ana", "otra-clave", "203.0.113.9") is None
    with pytest.raises(ExtrudeError):  # el cliente que falla queda bloqueado para esa cuenta
        mode.login("ana", "contraseña-larga-ana", "203.0.113.9")
    assert mode.login("ana", "contraseña-larga-ana", "198.51.100.4")  # el dueño, no


def test_a_client_trying_many_accounts_is_limited(tmp_path: Path) -> None:
    mode = _shared(tmp_path, max_failed_logins=5, max_failed_per_client=4)
    for i in range(4):
        mode.login(f"nombre-{i}", "x", "203.0.113.9")
    with pytest.raises(ExtrudeError):
        mode.login("ana", "contraseña-larga-ana", "203.0.113.9")


def test_rate_limiter_keys_are_bounded() -> None:
    limiter = RateLimiter(limit=5, window_s=300, max_keys=50)
    for i in range(500):
        limiter.allow(f"inventado-{i}")
    assert len(limiter) <= 50
    assert limiter.count("inventado-499") == 1  # la clave más reciente se conserva


def test_login_limits_use_the_forwarded_client_only_behind_the_proxy(tmp_path: Path) -> None:
    for trusted, locked_for_other_ip in ((True, False), (False, True)):
        mode = _shared(tmp_path / str(trusted), max_failed_logins=2, max_failed_per_client=50)
        instance, base = start(tmp_path / str(trusted), shared=mode, trust_forwarded=trusted)

        def login(password: str, ip: str, base: str = base) -> int:
            body = json.dumps({"user": "ana", "password": password}).encode()
            code, _ = call(
                f"{base}/api/auth/login",
                body,
                {"Content-Type": "application/json", "X-Forwarded-For": f"10.9.9.9, {ip}"},
            )
            return int(code)

        try:
            assert [login("mala", "203.0.113.9") for _ in range(2)] == [401, 401]
            code = login("contraseña-larga-ana", "198.51.100.4")
            assert (code == 429) is locked_for_other_ip
        finally:
            instance.shutdown()
            instance.server_close()


# --- M3: estados sin el informe en memoria --------------------------------------------------------


def test_finished_status_is_served_from_disk_not_memory(server: str) -> None:
    accepted = convert(server)
    final = final_state(server, accepted["status"])
    # El resultado completo se sigue sirviendo (leído de result.json)
    assert final["state"] == "done" and final["result"]["report"]["layers"]


def test_job_store_keeps_only_the_state_in_memory(tmp_path: Path) -> None:
    store = srv.JobStore(tmp_path)
    job_id, job_dir = store.create()
    (job_dir / srv.JOB_RESULT).write_text(json.dumps({"job": job_id, "big": "x" * 1000}))
    store.update(job_id, state="done", phase="done", fraction=1.0, result=None)
    assert store._statuses[job_id].result is None  # nada pesado en memoria
    assert store.status(job_id)["result"]["big"] == "x" * 1000  # type: ignore[index]
    (job_dir / srv.JOB_RESULT).unlink()
    gone = store.status(job_id)
    assert gone is not None and gone["state"] == "failed" and gone["http_status"] == 410


# --- B2: lo adaptado al terreno se mide ----------------------------------------------------


def test_draped_surfaces_have_their_contact_measured(tmp_path: Path) -> None:
    doc = ezdxf.new("R2018")
    doc.units = 6
    msp = doc.modelspace()
    for x in range(0, 101, 10):
        msp.add_lwpolyline([(x, 0), (x, 100)], dxfattribs={"layer": "T", "elevation": x / 5})
    msp.add_lwpolyline(
        [(10, 10), (60, 10), (60, 20), (10, 20)], close=True, dxfattribs={"layer": "V"}
    )
    path = tmp_path / "vial.dxf"
    doc.saveas(path)
    for offset, category in ((0.0, "supported"), (3.0, "floating")):
        config = parse_config(
            {
                "layers": [
                    {"match": "T", "kind": "terrain"},
                    {
                        "match": "V",
                        "kind": "road",
                        "height": 0.05,
                        "base_from_terrain": True,
                        "base_elevation": offset,
                    },
                ]
            }
        )
        layer = build_scene(read_dxf(path), config).layers["V"]
        (detail,) = layer.stats.ground_details
        low, high = detail["contact_clearance_m"]  # type: ignore[misc]
        assert low == pytest.approx(offset) and high == pytest.approx(offset)  # medido
        assert detail["category"] == category


# --- B3: solo EPSG UTM reales ----------------------------------------------------------------


def test_only_real_utm_epsg_codes_are_accepted() -> None:
    assert utm_zone("EPSG:25830") == (30, False)
    assert utm_zone("EPSG:32734") == (34, True)
    assert utm_zone("EPSG:32601") == (1, False)
    assert utm_zone("EPSG:25801") is None  # no existe
    assert to_wgs84("EPSG:25801", 440291, 4474254) is None
    lat, lon = to_wgs84("EPSG:25830", 440291, 4474254)  # type: ignore[misc]
    assert lat == pytest.approx(40.4168, abs=1e-4) and lon == pytest.approx(-3.7038, abs=1e-4)


# --- I4: subidas lentas ------------------------------------------------------------------------


def _slow_upload(base: str, headers: dict[str, str] | None = None) -> socket.socket:
    """Abre una subida válida de 100 kB y envía solo la cabecera y un byte."""
    host, port = base.removeprefix("http://").split(":")
    sock = socket.create_connection((host, int(port)), timeout=10)
    extra = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
    sock.sendall(
        f"POST /api/convert?name=lento.dxf&async=1 HTTP/1.1\r\nHost: {host}:{port}\r\n"
        f"Origin: {ORIGIN}\r\nContent-Type: application/octet-stream\r\n{extra}"
        f"Content-Length: 100000\r\n\r\n0".encode()
    )
    return sock


def _drip_until_answered(sock: socket.socket, seconds: float) -> bytes:
    """Gotea un byte cada 0,1 s hasta que el servidor responde (o se agota el tiempo)."""
    sock.settimeout(0.1)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            answer = sock.recv(4096)
            if answer:
                return answer
        except TimeoutError:
            pass
        with contextlib.suppress(OSError):
            sock.sendall(b"0")
    return b""


def test_slow_uploads_are_cut_and_free_the_conversion_queue(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(srv, "UPLOAD_GRACE_S", 0.3)
    monkeypatch.setattr(srv, "UPLOAD_MIN_BYTES_PER_S", 10_000)
    slow = [_slow_upload(server) for _ in range(srv.MAX_WAITING + 1)]
    try:
        time.sleep(0.2)
        code, _ = call(f"{server}/api/convert?name=p.dxf&async=1", DXF)
        assert code == 503  # la cola está ocupada por las subidas lentas
        answers = [_drip_until_answered(sock, 5) for sock in slow]
        assert all(b" 408 " in a.split(b"\r\n")[0] for a in answers), answers
        assert convert(server)["state"] == "pending"  # y el hueco vuelve a estar libre
    finally:
        for sock in slow:
            sock.close()


def test_shared_mode_limits_simultaneous_uploads_per_user(tmp_path: Path) -> None:
    mode = _shared(tmp_path)
    instance, base = start(tmp_path, shared=mode)
    body = json.dumps({"user": "ana", "password": "contraseña-larga-ana"}).encode()
    _, session = call(f"{base}/api/auth/login", body, {"Content-Type": "application/json"})
    auth = {"Authorization": f"Bearer {session['token']}"}
    slow = [_slow_upload(base, auth) for _ in range(srv.MAX_UPLOADS_PER_CLIENT)]
    try:
        time.sleep(0.2)
        code, error = call(f"{base}/api/convert?name=p.dxf&async=1", DXF, auth)
        assert code == 429, error
        assert "a la vez" in error["error"]
        for sock in slow:
            sock.close()
        deadline = time.monotonic() + TIMEOUT_S
        while (code := call(f"{base}/api/convert?name=p.dxf&async=1", DXF, auth)[0]) == 429:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert code == 202  # al cortarse las subidas, el usuario vuelve a poder subir
    finally:
        instance.shutdown()
        instance.server_close()
