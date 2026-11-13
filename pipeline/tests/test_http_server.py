"""El límite HTTP se aplica también a cabeceras lentas, antes de leer el cuerpo."""

import http.client
import threading
from http.server import BaseHTTPRequestHandler

from extrude.http_server import BoundedHTTPServer


def test_http_admission_releases_slots_after_response() -> None:
    entered = threading.Event()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            entered.set()
            assert release.wait(5)
            self.send_response(204)
            self.end_headers()

    server = BoundedHTTPServer(("127.0.0.1", 0), Handler, max_connections=1)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    first = http.client.HTTPConnection(*server.server_address, timeout=5)
    second = http.client.HTTPConnection(*server.server_address, timeout=5)
    try:
        first.request("GET", "/")
        assert entered.wait(5)
        # No enviar cuerpo ni cabeceras: la admisión precede al manejador.
        second.connect()
        raw = second.sock.recv(4096)
        assert b"503 Service Unavailable" in raw
        assert b"Retry-After: 30" in raw
        assert raw.endswith(b'{"error":"server_busy"}')
        assert int(raw.split(b"Content-Length: ")[1].split(b"\r\n")[0]) == len(
            raw.split(b"\r\n\r\n")[1]
        )
        release.set()
        assert first.getresponse().status == 204
    finally:
        release.set()
        first.close()
        second.close()
        server.shutdown()
        server.server_close()
        thread.join(5)
    assert server._slots.acquire(blocking=False)
