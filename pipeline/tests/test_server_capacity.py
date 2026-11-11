"""Capacidad acotada de extrude serve: cola, rechazo 503, timeout y retención de trabajos."""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from extrude import server as srv
from extrude.server import BUSY_RETRY_AFTER_S, MAX_JOBS, MAX_WAITING, create_server

DXF = b"0\nEOF\n"
TIMEOUT_S = 20


def call(url: str, data: bytes | None = None) -> tuple[int, dict[str, str], dict[str, Any]]:
    req = urllib.request.Request(url, data=data)  # noqa: S310 (URL local)
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310 (URL local del test)
            return resp.status, dict(resp.headers), json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), json.loads(err.read())


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> Iterator[threading.Event]:
    """Sustituye la conversión real por una que espera a que el test abra la puerta."""
    opened = threading.Event()

    def fake_finish(job_id: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        assert opened.wait(TIMEOUT_S)
        return {"job": job_id}

    monkeypatch.setattr(srv, "finish_job", fake_finish)
    yield opened
    opened.set()


@pytest.fixture
def base(tmp_path: Path) -> Iterator[str]:
    server = create_server("127.0.0.1", 0, jobs_dir=tmp_path / "jobs", max_mb=1)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def jobs_on_disk(tmp_path: Path) -> int:
    return sum(1 for p in (tmp_path / "jobs").iterdir() if p.is_dir())


def wait_until(predicate: Any) -> None:
    deadline = time.monotonic() + TIMEOUT_S
    while not predicate():
        assert time.monotonic() < deadline, "no se alcanzó el estado esperado"
        time.sleep(0.01)


def test_full_server_rejects_with_503_before_reading_or_storing(
    base: str, tmp_path: Path, gate: threading.Event
) -> None:
    """La evidencia de la auditoría: 12 peticiones con una conversión bloqueada."""
    capacity = MAX_WAITING + 1
    answers = [call(f"{base}/api/convert?name=p{i}.dxf&async=1", DXF) for i in range(12)]
    codes = [code for code, _, _ in answers]
    assert codes == [202] * capacity + [503] * (12 - capacity)
    _, headers, body = answers[-1]
    assert headers["Retry-After"] == str(BUSY_RETRY_AFTER_S)
    assert "reintenta" in body["fix"]
    assert jobs_on_disk(tmp_path) == capacity  # los rechazados no crean carpeta
    assert call(f"{base}/api/health")[2]["queue"] == {"in_use": capacity, "capacity": capacity}

    gate.set()  # terminan todos: se libera la capacidad
    wait_until(lambda: call(f"{base}/api/health")[2]["queue"]["in_use"] == 0)
    for _, _, accepted in answers[:capacity]:
        assert call(base + accepted["status"])[2]["state"] == "done"
    assert call(f"{base}/api/convert?name=otra.dxf&async=1", DXF)[0] == 202


def test_sync_requests_share_the_same_capacity(
    base: str, tmp_path: Path, gate: threading.Event, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(srv, "run_job", lambda *a, **k: (gate.wait(TIMEOUT_S), {"ok": True})[1])
    capacity = MAX_WAITING + 1
    for i in range(capacity):
        assert call(f"{base}/api/convert?name=a{i}.dxf&async=1", DXF)[0] == 202
    assert call(f"{base}/api/convert?name=sync.dxf", DXF)[0] == 503  # síncrona: tampoco cabe


def test_finished_jobs_are_pruned_when_they_finish(
    base: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(srv, "finish_job", lambda job_id, *a, **k: {"job": job_id})
    accepted = []
    for i in range(MAX_JOBS + 6):
        code, _, body = call(f"{base}/api/convert?name=p{i}.dxf&async=1", DXF)
        assert code == 202
        accepted.append(body)
        wait_until(lambda b=body: call(base + b["status"])[2]["state"] == "done")
    # el trabajador poda justo después de terminar cada trabajo
    wait_until(lambda: jobs_on_disk(tmp_path) == MAX_JOBS)
    assert call(base + accepted[-1]["status"])[0] == 200  # el más reciente sigue disponible
    assert call(base + accepted[0]["status"])[0] == 404  # el más antiguo, podado con su estado


def test_stalled_client_times_out_and_frees_its_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(srv, "REQUEST_TIMEOUT_S", 0.5)
    server = create_server("127.0.0.1", 0, jobs_dir=tmp_path / "jobs", max_mb=1)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    try:
        with socket.create_connection((str(host), int(port)), timeout=TIMEOUT_S) as sock:
            # Anuncia 1000 bytes y no envía ninguno
            sock.sendall(
                b"POST /api/convert?name=p.dxf&async=1 HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                b"Content-Length: 1000\r\n\r\n"
            )
            assert sock.recv(1024) == b""  # el servidor cierra la conexión
        base = f"http://{host}:{port}"
        wait_until(lambda: call(f"{base}/api/health")[2]["queue"]["in_use"] == 0)
        assert jobs_on_disk(tmp_path) == 0
    finally:
        server.shutdown()
        server.server_close()


def test_full_queue_responds_before_client_sends_any_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(srv.ConversionQueue, "reserve", lambda self: False)
    monkeypatch.setattr(srv, "REQUEST_TIMEOUT_S", 0.3)
    server = create_server("127.0.0.1", 0, jobs_dir=tmp_path / "jobs", max_mb=1)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    try:
        with socket.create_connection((str(host), int(port)), timeout=2) as sock:
            sock.sendall(
                b"POST /api/convert?name=p.dxf HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                b"Content-Length: 100\r\n\r\n"
            )
            response = http.client.HTTPResponse(sock)
            response.begin()
            assert response.status == 503
            assert response.getheader("Retry-After") == str(BUSY_RETRY_AFTER_S)
            assert response.getheader("Connection") == "close"
            assert "reintenta" in json.loads(response.read())["fix"]
        assert jobs_on_disk(tmp_path) == 0
    finally:
        server.shutdown()
        server.server_close()
