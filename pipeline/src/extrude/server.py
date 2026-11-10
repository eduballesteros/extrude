"""Servidor HTTP local (`extrude serve`): el visor envía un DWG/DXF y recibe el GLB.

Solo librería estándar. Pensado para uso local (escucha en 127.0.0.1 por defecto):
  GET  /api/health                     → conversores disponibles
  POST /api/convert?name=plano.dwg     → cuerpo = bytes del DWG/DXF; cabecera opcional
       [&tiles=500&compress=meshopt]     X-Extrude-Config = YAML en Base64 (si falta, se genera
                                         un borrador con init-config y se devuelve)
  GET  /api/jobs/<id>/<archivo>        → model.glb, report.md, report.json, config.yaml

Seguridad: el archivo subido se guarda con nombre fijo (solo se usa su extensión), con límite
de tamaño; los ids de trabajo se validan; CORS solo para los orígenes indicados; una
conversión a la vez (CPU intensiva); se conservan los últimos `MAX_JOBS` trabajos.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

from extrude import __version__
from extrude.config import load_config, parse_config
from extrude.convert import ConvertOptions, convert
from extrude.converters import converter_statuses, ensure_dxf
from extrude.dxf_reader import read_dxf
from extrude.errors import ExtrudeError
from extrude.init_config import draft_config
from extrude.node_tools import Compression

DEFAULT_PORT = 8765
DEFAULT_MAX_MB = 500
DEFAULT_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
MAX_JOBS = 10
DISCARD_LIMIT_FACTOR = 4
ALLOWED_INPUTS = {".dwg", ".dxf"}
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
JOB_FILES = {
    "model.glb": "model/gltf-binary",
    "report.md": "text/markdown; charset=utf-8",
    "report.json": "application/json",
    "config.yaml": "application/yaml; charset=utf-8",
    "manifest.json": "application/json",
}
CONFIG_HEADER = "X-Extrude-Config"


class RequestError(Exception):
    def __init__(self, status: HTTPStatus, problem: str, fix: str) -> None:
        super().__init__(problem)
        self.status = status
        self.problem = problem
        self.fix = fix


@dataclass(frozen=True)
class ServerSettings:
    jobs_dir: Path
    max_bytes: int
    allowed_origins: tuple[str, ...]


class JobStore:
    """Carpetas de trabajo en `jobs_dir`; se borran las más antiguas por encima de MAX_JOBS."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self) -> tuple[str, Path]:
        job_id = uuid.uuid4().hex
        path = self.root / job_id
        path.mkdir()
        self._prune()
        return job_id, path

    def file(self, job_id: str, name: str) -> Path | None:
        if not JOB_ID.match(job_id) or name not in JOB_FILES:
            return None
        path = self.root / job_id / "out" / name
        if name == "config.yaml":
            path = self.root / job_id / name
        return path if path.is_file() else None

    def _prune(self) -> None:
        jobs = sorted(
            (p for p in self.root.iterdir() if p.is_dir() and JOB_ID.match(p.name)),
            key=lambda p: p.stat().st_mtime,
        )
        for old in jobs[:-MAX_JOBS]:
            shutil.rmtree(old, ignore_errors=True)


def _decode_config(header: str | None) -> str | None:
    if not header:
        return None
    try:
        return base64.b64decode(header, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise RequestError(
            HTTPStatus.BAD_REQUEST,
            f"La cabecera {CONFIG_HEADER} no es YAML en Base64 UTF-8.",
            "Envía el YAML codificado en Base64.",
        ) from exc


def _with_user_name(exc: ExtrudeError, internal: str, original: str) -> ExtrudeError:
    """Los mensajes citan el archivo interno (input.dxf): mostrar el nombre del usuario."""
    return type(exc)(exc.problem.replace(internal, original), exc.fix.replace(internal, original))


def run_job(
    store: JobStore, data: bytes, name: str, params: dict[str, list[str]], config_text: str | None
) -> dict[str, Any]:
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_INPUTS:
        raise RequestError(
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            f"Formato '{suffix or '(sin extensión)'}' no admitido.",
            "El archivo de entrada debe ser .dwg o .dxf.",
        )
    job_id, job_dir = store.create()
    source = job_dir / f"input{suffix}"
    source.write_bytes(data)
    try:
        return _convert_job(job_id, job_dir, source, name, params, config_text)
    except ExtrudeError as exc:
        raise _with_user_name(exc, source.name, Path(name).name) from exc


def _convert_job(
    job_id: str,
    job_dir: Path,
    source: Path,
    name: str,
    params: dict[str, list[str]],
    config_text: str | None,
) -> dict[str, Any]:
    config_path = job_dir / "config.yaml"
    generated = config_text is None
    if generated:
        with tempfile.TemporaryDirectory(prefix="extrude-") as workdir:
            config_text = draft_config(read_dxf(ensure_dxf(source, Path(workdir))), Path(name).name)
    if config_text is None:  # draft_config siempre devuelve texto; para el tipado
        raise RequestError(HTTPStatus.INTERNAL_SERVER_ERROR, "Sin configuración.", "Reintenta.")
    parse_config(yaml.safe_load(config_text), source="config")  # error claro si no es válida
    config_path.write_text(config_text, encoding="utf-8")

    tiles = _number(params, "tiles")
    compress_raw = params.get("compress", [""])[0]
    compression = Compression(compress_raw) if compress_raw else None
    options = ConvertOptions(load_config(config_path), job_dir / "out", tiles, compression)
    result = convert(source, options)
    base = f"/api/jobs/{job_id}"
    return {
        "job": job_id,
        "source": name,
        "config_generated": generated,
        "files": {
            "glb": f"{base}/model.glb" if (job_dir / "out" / "model.glb").is_file() else None,
            "manifest": f"{base}/manifest.json" if tiles else None,
            "report_md": f"{base}/report.md",
            "report_json": f"{base}/report.json",
            "config": f"{base}/config.yaml",
        },
        "report": result.report,
    }


def _number(params: dict[str, list[str]], key: str) -> float | None:
    raw = params.get(key, [""])[0]
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise RequestError(
            HTTPStatus.BAD_REQUEST, f"'{key}' no es un número.", "Ej.: tiles=500"
        ) from exc
    if value <= 0:
        raise RequestError(HTTPStatus.BAD_REQUEST, f"'{key}' debe ser > 0.", "Ej.: tiles=500")
    return value


def make_handler(settings: ServerSettings, store: JobStore) -> type[BaseHTTPRequestHandler]:
    convert_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = f"extrude/{__version__}"

        def log_message(self, format: str, *args: Any) -> None:
            print(f"[extrude serve] {self.address_string()} {format % args}", flush=True)

        # --- utilidades ---------------------------------------------------
        def _cors(self) -> None:
            origin = self.headers.get("Origin")
            if origin and origin in settings.allowed_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", f"Content-Type, {CONFIG_HEADER}")
                self.send_header("Access-Control-Allow-Private-Network", "true")

        def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _discard(self, length: int) -> None:
            """Lee y descarta un cuerpo rechazado para que el cliente reciba la respuesta (si no,
            en Windows suele ver un corte de conexión). Si es desmesurado, se cierra sin leer."""
            if length > DISCARD_LIMIT_FACTOR * settings.max_bytes:
                self.close_connection = True
                return
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 1 << 20))
                if not chunk:
                    break
                remaining -= len(chunk)

        def _error(self, status: HTTPStatus, problem: str, fix: str) -> None:
            self._json(status, {"error": problem, "fix": fix})

        # --- rutas --------------------------------------------------------
        def do_OPTIONS(self) -> None:
            self.send_response(HTTPStatus.NO_CONTENT)
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:
            parts = urlparse(self.path).path.strip("/").split("/")
            if parts == ["api", "health"]:
                converters = [
                    {"name": s.name, "available": s.available} for s in converter_statuses()
                ]
                self._json(
                    HTTPStatus.OK, {"ok": True, "version": __version__, "converters": converters}
                )
                return
            if len(parts) == 4 and parts[:2] == ["api", "jobs"]:
                path = store.file(parts[2], parts[3])
                if path is None:
                    self._error(
                        HTTPStatus.NOT_FOUND,
                        "No existe ese archivo de trabajo.",
                        "Repite la conversión.",
                    )
                    return
                data = path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self._cors()
                self.send_header("Content-Type", JOB_FILES[parts[3]])
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self._error(
                HTTPStatus.NOT_FOUND, "Ruta desconocida.", "Usa /api/health o /api/convert."
            )

        def do_POST(self) -> None:
            url = urlparse(self.path)
            if url.path != "/api/convert":
                self._error(HTTPStatus.NOT_FOUND, "Ruta desconocida.", "Usa POST /api/convert.")
                return
            params = parse_qs(url.query)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    raise RequestError(
                        HTTPStatus.BAD_REQUEST, "Cuerpo vacío.", "Envía el DWG/DXF en el cuerpo."
                    )
                if length > settings.max_bytes:
                    self._discard(length)
                    raise RequestError(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        f"El archivo supera el límite de {settings.max_bytes // 1_048_576} MB.",
                        "Arranca el servidor con --max-mb mayor o divide el dibujo.",
                    )
                data = self.rfile.read(length)
                config_text = _decode_config(self.headers.get(CONFIG_HEADER))
                name = params.get("name", ["plano.dxf"])[0]
                with convert_lock:
                    payload = run_job(store, data, name, params, config_text)
                self._json(HTTPStatus.OK, payload)
            except RequestError as exc:
                self._error(exc.status, exc.problem, exc.fix)
            except ExtrudeError as exc:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.problem, exc.fix)
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc), "Revisa los parámetros.")
            except (
                Exception
            ) as exc:  # error inesperado: respuesta JSON en vez de cortar la conexión
                self.log_error("Error inesperado: %r", exc)
                self._error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    f"Error inesperado al convertir: {exc}",
                    "Revisa la consola de extrude serve y prueba con extrude convert.",
                )

    return Handler


def create_server(
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    *,
    jobs_dir: Path | None = None,
    max_mb: int = DEFAULT_MAX_MB,
    allowed_origins: tuple[str, ...] = DEFAULT_ORIGINS,
) -> ThreadingHTTPServer:
    root = jobs_dir or Path(tempfile.gettempdir()) / "extrude-jobs"
    settings = ServerSettings(root, max_mb * 1_048_576, allowed_origins)
    return ThreadingHTTPServer((host, port), make_handler(settings, JobStore(root)))
