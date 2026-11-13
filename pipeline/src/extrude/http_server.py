"""Admisión HTTP acotada antes de crear hilos o leer cuerpos de petición."""

from __future__ import annotations

import socket
import threading
from http.server import ThreadingHTTPServer
from typing import Any

MAX_HTTP_CONNECTIONS = 16


class BoundedHTTPServer(ThreadingHTTPServer):
    """Un único proceso; las conexiones lentas también consumen un hueco."""

    def __init__(
        self, *args: Any, max_connections: int = MAX_HTTP_CONNECTIONS, **kwargs: Any
    ) -> None:
        if max_connections < 1:
            raise ValueError("max_connections debe ser positivo")
        self._slots = threading.BoundedSemaphore(max_connections)
        super().__init__(*args, **kwargs)

    def process_request(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: Any
    ) -> None:
        if not isinstance(request, socket.socket):
            raise TypeError("HTTP requiere un socket TCP")
        if not self._slots.acquire(blocking=False):
            try:
                request.settimeout(1)
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Connection: close\r\nRetry-After: 30\r\n"
                    b"Content-Type: application/json\r\nContent-Length: 23\r\n\r\n"
                    b'{"error":"server_busy"}'
                )
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: Any
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()
