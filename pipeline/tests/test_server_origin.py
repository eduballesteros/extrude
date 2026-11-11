"""Solo el visor permitido (o un cliente sin navegador) puede iniciar conversiones."""

from __future__ import annotations

import http.client
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from extrude.server import UPLOAD_CONTENT_TYPE, create_server, host_name

ALLOWED = "http://localhost:5173"
EVIL = "https://evil.example"


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[str, int, Path]]:
    srv = create_server("127.0.0.1", 0, jobs_dir=tmp_path / "jobs", max_mb=1)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    yield str(host), int(port), tmp_path / "jobs"
    srv.shutdown()
    srv.server_close()


def send(
    server: tuple[str, int, Path],
    method: str,
    path: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str]]:
    host, port, _ = server
    conn = http.client.HTTPConnection(host, port, timeout=30)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        resp.read()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}
    finally:
        conn.close()


def jobs(server: tuple[str, int, Path]) -> int:
    root = server[2]
    return sum(1 for p in root.iterdir() if p.is_dir()) if root.exists() else 0


def test_foreign_origin_cannot_start_a_conversion(server: tuple[str, int, Path]) -> None:
    """La evidencia de la auditoría: Origin ajeno + text/plain devolvía 202 y creaba el trabajo."""
    for content_type in ("text/plain", UPLOAD_CONTENT_TYPE):
        code, headers = send(
            server,
            "POST",
            "/api/convert?name=p.dxf&async=1",
            b"0\nEOF\n",
            {"Origin": EVIL, "Content-Type": content_type},
        )
        assert code == 403
        assert "access-control-allow-origin" not in headers
    assert jobs(server) == 0


def test_allowed_origin_must_send_octet_stream(server: tuple[str, int, Path]) -> None:
    """text/plain sería una «petición simple» sin comprobación previa CORS: se rechaza."""
    for content_type in ("text/plain", "application/x-www-form-urlencoded", ""):
        code, _ = send(
            server,
            "POST",
            "/api/convert?name=p.dxf&async=1",
            b"0\nEOF\n",
            {"Origin": ALLOWED, "Content-Type": content_type},
        )
        assert code == 415
    assert jobs(server) == 0
    code, headers = send(
        server,
        "POST",
        "/api/convert?name=p.dxf&async=1",
        b"0\nEOF\n",
        {"Origin": ALLOWED, "Content-Type": f"{UPLOAD_CONTENT_TYPE}; charset=binary"},
    )
    assert code == 202
    assert headers["access-control-allow-origin"] == ALLOWED


def test_clients_without_origin_keep_working(server: tuple[str, int, Path]) -> None:
    """curl y los scripts no envían Origin: cualquier Content-Type, como antes."""
    code, _ = send(
        server,
        "POST",
        "/api/convert?name=p.dxf&async=1",
        b"0\nEOF\n",
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert code == 202


def test_preflight_only_for_allowed_origins(server: tuple[str, int, Path]) -> None:
    preflight = {"Access-Control-Request-Method": "POST"}
    code, headers = send(server, "OPTIONS", "/api/convert", None, {"Origin": EVIL, **preflight})
    assert code == 403
    assert "access-control-allow-origin" not in headers
    code, headers = send(server, "OPTIONS", "/api/convert", None, {"Origin": ALLOWED, **preflight})
    assert code == 204
    assert headers["access-control-allow-origin"] == ALLOWED


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8765", "192.168.1.20:8765"])
def test_dns_rebinding_host_is_refused(server: tuple[str, int, Path], host: str) -> None:
    for method, path, body in (
        ("GET", "/api/health", None),
        ("POST", "/api/convert?name=p.dxf&async=1", b"0\nEOF\n"),
    ):
        code, _ = send(server, method, path, body, {"Host": host})
        assert code == 421
    assert jobs(server) == 0


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "LOCALHOST", "[::1]:8765"])
def test_loopback_hosts_are_accepted(server: tuple[str, int, Path], host: str) -> None:
    assert send(server, "GET", "/api/health", None, {"Host": host})[0] == 200


@pytest.mark.parametrize(
    ("header", "name"),
    [
        ("127.0.0.1:8765", "127.0.0.1"),
        ("localhost", "localhost"),
        ("[::1]:8765", "::1"),
        ("::1", "::1"),
        ("Example.COM:80", "example.com"),
        (None, ""),
    ],
)
def test_host_name(header: str | None, name: str) -> None:
    assert host_name(header) == name
