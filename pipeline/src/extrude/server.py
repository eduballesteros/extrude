"""Servidor HTTP local (`extrude serve`): el visor envía un DWG/DXF y recibe el GLB.

Solo librería estándar. Pensado para uso local (escucha en 127.0.0.1 por defecto):
  GET  /api/health                     → conversores disponibles
  POST /api/convert?name=plano.dwg     → cuerpo = bytes del DWG/DXF; cabecera opcional
       [&tiles=500&compress=meshopt]     X-Extrude-Config = YAML en Base64 (si falta, se genera
                                         un borrador con init-config y se devuelve)
       [&async=1]                        → 202 {job, state, status} al instante; la conversión
                                         sigue en segundo plano
  GET  /api/jobs/<id>/status           → {state, phase, fraction, result | error, fix}
  GET  /api/jobs/<id>/<archivo>        → model.glb, report.md, report.json, config.yaml,
                                         objects.json (catálogo de objetos), manifest.json,
                                         tiles/tile_<i>_<j>.glb (con tiles=), materials.json,
                                         model.preview.glb y model.materialized.glb
  GET  /api/library                    → biblioteca de materiales (IDs, versiones, colores)
  GET  /api/library/<patrón>.png       → textura de un material (miniaturas del visor)
  POST /api/models                     → cuerpo = GLB base (sin comprimir); crea un trabajo para
                                         materializar un modelo abierto desde disco
  POST /api/jobs/<id>/materials        → cuerpo = materials.json (application/json);
       [?preview=1]                      valida y materializa: preview → model.preview.glb;
                                         sin preview confirma una revisión nueva (mat-00N)

Seguridad: el archivo subido se guarda con nombre fijo (solo se usa su extensión), con límite
de tamaño; los ids de trabajo y los nombres de archivo se validan con una lista blanca (sin
rutas arbitrarias: nada de `..`, rutas absolutas ni otras subcarpetas); CORS solo para los
orígenes indicados; una conversión a la vez (CPU intensiva); se conservan los últimos
`MAX_JOBS` trabajos.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import queue
import re
import shutil
import tempfile
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from extrude import __version__
from extrude.config import load_config
from extrude.convert import ConvertOptions, convert
from extrude.converters import converter_statuses, ensure_dxf
from extrude.dxf_reader import read_dxf
from extrude.errors import ExtrudeError
from extrude.init_config import draft_config
from extrude.materials_service import (
    BASE_GLB,
    PlanRejected,
    apply_plan,
    inspect_base,
    library_payload,
    texture_png,
)
from extrude.node_tools import Compression

DEFAULT_PORT = 8765
DEFAULT_MAX_MB = 500
DEFAULT_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
MAX_JOBS = 10  # trabajos terminados que se conservan (los activos van aparte, ver MAX_WAITING)
# Capacidad: una conversión en curso y hasta MAX_WAITING esperando (síncronas o asíncronas).
# Por encima, 503 con Retry-After sin leer el cuerpo: ni hilos, ni carpetas, ni memoria sin límite.
MAX_WAITING = 4
BUSY_RETRY_AFTER_S = 30
# Timeout de lectura por operación del socket: un cliente que deja de enviar libera el hilo
REQUEST_TIMEOUT_S = 30
DISCARD_LIMIT_FACTOR = 4
MAX_PLAN_BODY = 5 * 1024 * 1024
ALLOWED_INPUTS = {".dwg", ".dxf"}
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
JOB_FILES = {
    "model.glb": "model/gltf-binary",
    "report.md": "text/markdown; charset=utf-8",
    "report.json": "application/json",
    "config.yaml": "application/yaml; charset=utf-8",
    "manifest.json": "application/json",
    "objects.json": "application/json",
    "materials.json": "application/json",
    "model.preview.glb": "model/gltf-binary",
    "model.materialized.glb": "model/gltf-binary",
}
PLAN_CONTENT_TYPE = "application/json"
MATERIALS_PATH = re.compile(r"^/api/jobs/([0-9a-f]{32})/materials$")
LIBRARY_TEXTURE = re.compile(r"^/api/library/([a-z]{1,32})\.png$")
# Los INSERT pueden quedar fuera del bbox geométrico: los índices de convert._write_tiles
# son enteros con signo, sin límite de seis dígitos. Solo se admiten dígitos ASCII.
TILE_FILE = re.compile(r"tiles/tile_-?[0-9]+_-?[0-9]+\.glb")
TILE_CONTENT_TYPE = "model/gltf-binary"
CONFIG_HEADER = "X-Extrude-Config"

# Fracción aproximada del trabajo al empezar cada fase de convert() (orden de las fases)
PHASE_START = {
    "queued": 0.0,
    "dwg_to_dxf": 0.02,
    "read": 0.05,
    "build": 0.5,
    "instancing": 0.8,
    "export": 0.88,
    "compress": 0.94,
    "done": 1.0,
}
# Sin YAML se lee el dibujo una vez más para el borrador: ocupa esta parte de la barra
DRAFT_SHARE = 0.3
ACTIVE_STATES = ("pending", "running")


@dataclass
class JobStatus:
    """Estado de un trabajo asíncrono (lo consulta el visor con GET .../status)."""

    state: str = "pending"  # pending | running | done | failed
    phase: str = "queued"  # queued, draft_config, dwg_to_dxf, read, build, instancing, export…
    fraction: float = 0.0
    result: dict[str, Any] | None = None
    error: str | None = None
    fix: str | None = None
    http_status: int | None = None

    def as_dict(self, job_id: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "job": job_id,
            "state": self.state,
            "phase": self.phase,
            "fraction": round(self.fraction, 3),
        }
        if self.result is not None:
            payload["result"] = self.result
        if self.error is not None:
            payload.update(error=self.error, fix=self.fix, http_status=self.http_status)
        return payload


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
    # Escuchando en loopback, solo se aceptan esos nombres en Host (contra DNS rebinding)
    loopback_only: bool = True


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
UPLOAD_CONTENT_TYPE = "application/octet-stream"


def host_name(header: str | None) -> str:
    """Nombre de la cabecera Host sin puerto ni corchetes IPv6, en minúsculas."""
    value = (header or "").strip().lower()
    if value.startswith("["):  # [::1]:8765
        return value[1 : value.find("]")] if "]" in value else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


class JobStore:
    """Carpetas de trabajo en `jobs_dir`. Se conservan todas las activas (acotadas por la
    capacidad del servidor) y las MAX_JOBS terminadas más recientes."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._statuses: dict[str, JobStatus] = {}
        # Trabajos activos (desde que existe su carpeta hasta finish()): nunca son candidatos a
        # poda. Se reservan en create() bajo el mismo lock que la poda usa para elegir.
        self._active: set[str] = set()
        self._lock = threading.Lock()
        self._prune_lock = threading.Lock()  # una poda a la vez (elección + borrado)

    def update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            status = self._statuses.setdefault(job_id, JobStatus())
            for key, value in changes.items():
                setattr(status, key, value)

    def status(self, job_id: str) -> dict[str, Any] | None:
        if not JOB_ID.match(job_id):
            return None
        with self._lock:
            status = self._statuses.get(job_id)
            return status.as_dict(job_id) if status else None

    def create(self) -> tuple[str, Path]:
        """Crea la carpeta y la registra como activa de forma atómica respecto a la poda."""
        job_id = uuid.uuid4().hex
        path = self.root / job_id
        with self._lock:
            path.mkdir()
            self._active.add(job_id)
        self.prune()
        return job_id, path

    def finish(self, job_id: str) -> None:
        """El trabajo terminó (bien o mal): desde ahora cuenta para la retención."""
        with self._lock:
            self._active.discard(job_id)

    def discard(self, job_id: str) -> None:
        """Trabajo abortado antes de encolarse: fuera de activos y su carpeta, borrada."""
        self.finish(job_id)
        shutil.rmtree(self.root / job_id, ignore_errors=True)

    def is_active(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._active

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    def file(self, job_id: str, name: str) -> tuple[Path, str] | None:
        """(ruta, Content-Type) de un archivo servible del trabajo, o None.

        `name` es la ruta relativa de la URL: uno de JOB_FILES o una tesela `tiles/tile_i_j.glb`.
        Cualquier otra cosa (`..`, barras invertidas, rutas absolutas, otras extensiones) se
        rechaza.
        """
        if not JOB_ID.match(job_id):
            return None
        if name in JOB_FILES:
            content_type = JOB_FILES[name]
        elif TILE_FILE.fullmatch(name):
            content_type = TILE_CONTENT_TYPE
        else:
            return None
        job = self.root / job_id
        base = job if name == "config.yaml" else job / "out"
        path = (base / name).resolve()
        if not path.is_relative_to(base.resolve()) or not path.is_file():
            return None  # defensa adicional: nunca fuera de la carpeta del trabajo
        return path, content_type

    def prune(self, keep: int = MAX_JOBS) -> None:
        """Borra los trabajos terminados más antiguos por encima de `keep` (y sus estados).
        Los activos (en cola o convirtiéndose) no cuentan ni se tocan.

        La lista de carpetas y la de activos se toman juntas bajo el lock de create(): una
        carpeta recién creada o ya está reservada o aún no existe, nunca «huérfana». Los
        candidatos son trabajos terminados, que no vuelven a activarse: borrarlos fuera del lock
        es seguro, y `_prune_lock` evita dos podas a la vez."""
        with self._prune_lock:
            with self._lock:
                active = self._active | {
                    j for j, s in self._statuses.items() if s.state in ACTIVE_STATES
                }
                finished = sorted(
                    (
                        p
                        for p in self.root.iterdir()
                        if p.is_dir() and JOB_ID.match(p.name) and p.name not in active
                    ),
                    key=lambda p: p.stat().st_mtime,
                )
            for old in finished[: max(len(finished) - keep, 0)]:
                shutil.rmtree(old, ignore_errors=True)
                with self._lock:
                    self._statuses.pop(old.name, None)

    @property
    def status_count(self) -> int:
        with self._lock:
            return len(self._statuses)


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


def start_job(store: JobStore, data: bytes, name: str) -> tuple[str, Path, Path]:
    """Valida la extensión, crea la carpeta del trabajo y guarda el plano subido."""
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_INPUTS:
        raise RequestError(
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            f"Formato '{suffix or '(sin extensión)'}' no admitido.",
            "El archivo de entrada debe ser .dwg o .dxf.",
        )
    job_id, job_dir = store.create()
    source = job_dir / f"input{suffix}"
    try:
        source.write_bytes(data)
    except BaseException:
        store.discard(job_id)
        raise
    return job_id, job_dir, source


def finish_job(
    job_id: str,
    job_dir: Path,
    source: Path,
    name: str,
    params: dict[str, list[str]],
    config_text: str | None,
    on_phase: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    try:
        return _convert_job(job_id, job_dir, source, name, params, config_text, on_phase)
    except ExtrudeError as exc:
        raise _with_user_name(exc, source.name, Path(name).name) from exc


def run_job(
    store: JobStore, data: bytes, name: str, params: dict[str, list[str]], config_text: str | None
) -> dict[str, Any]:
    job_id, job_dir, source = start_job(store, data, name)
    try:
        return finish_job(job_id, job_dir, source, name, params, config_text)
    finally:
        store.finish(job_id)


def validate_params(params: dict[str, list[str]]) -> None:
    """Errores de parámetros antes de aceptar un trabajo asíncrono (no a mitad)."""
    _number(params, "tiles")
    compress_raw = params.get("compress", [""])[0]
    if compress_raw:
        try:
            Compression(compress_raw)
        except ValueError as exc:
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                f"compress='{compress_raw}' no válido.",
                "Usa compress=draco o compress=meshopt.",
            ) from exc


def phase_fraction(phase: str, *, drafting: bool) -> float:
    """Fracción global al empezar `phase`; con borrador, la conversión ocupa el resto."""
    if phase == "draft_config":
        return 0.0
    start = PHASE_START.get(phase, 0.0)
    return DRAFT_SHARE + (1 - DRAFT_SHARE) * start if drafting else start


def run_job_async(
    store: JobStore,
    lock: threading.Lock,
    job: tuple[str, Path, Path],
    name: str,
    params: dict[str, list[str]],
    config_text: str | None,
) -> None:
    """Cuerpo del hilo de un trabajo asíncrono: una conversión a la vez, estado en `store`."""
    job_id, job_dir, source = job
    drafting = config_text is None

    def on_phase(phase: str) -> None:
        store.update(
            job_id, state="running", phase=phase, fraction=phase_fraction(phase, drafting=drafting)
        )

    try:
        with lock:
            on_phase("draft_config" if drafting else "dwg_to_dxf")
            result = finish_job(job_id, job_dir, source, name, params, config_text, on_phase)
        # Primero deja de estar activo y luego se publica: quien vea «done» y envíe otro trabajo
        # encuentra este ya contado como terminado por la poda.
        store.finish(job_id)
        store.update(job_id, state="done", phase="done", fraction=1.0, result=result)
    except RequestError as exc:
        _failed(store, job_id, exc.status, exc.problem, exc.fix)
    except ExtrudeError as exc:
        _failed(store, job_id, HTTPStatus.UNPROCESSABLE_ENTITY, exc.problem, exc.fix)
    except ValueError as exc:
        _failed(store, job_id, HTTPStatus.BAD_REQUEST, str(exc), "Revisa los parámetros.")
    except Exception as exc:  # el hilo no debe morir en silencio: el visor espera un estado
        print(f"[extrude serve] Error inesperado en {job_id}: {exc!r}", flush=True)
        _failed(
            store,
            job_id,
            HTTPStatus.INTERNAL_SERVER_ERROR,
            f"Error inesperado al convertir: {exc}",
            "Revisa la consola de extrude serve y prueba con extrude convert.",
        )


class ConversionQueue:
    """Capacidad del servidor y trabajador de las conversiones asíncronas.

    `slots` cuenta las conversiones admitidas (1 en curso + MAX_WAITING esperando), síncronas o
    asíncronas: se reserva antes de leer el cuerpo y se libera al terminar. Las asíncronas las
    ejecuta un único hilo trabajador, en orden de llegada; `lock` serializa ambas.
    """

    def __init__(self, store: JobStore, capacity: int = MAX_WAITING + 1) -> None:
        self.store = store
        self.capacity = capacity
        self.lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(capacity)
        self._busy = 0
        self._busy_lock = threading.Lock()
        self._jobs: queue.Queue[
            tuple[tuple[str, Path, Path], str, dict[str, list[str]], str | None]
        ]
        self._jobs = queue.Queue()
        self._worker = threading.Thread(target=self._work, name="extrude-worker", daemon=True)
        self._worker.start()

    @property
    def in_use(self) -> int:
        with self._busy_lock:
            return self._busy

    def reserve(self) -> bool:
        """Reserva un hueco sin esperar; False si el servidor está lleno."""
        if not self._slots.acquire(blocking=False):
            return False
        with self._busy_lock:
            self._busy += 1
        return True

    def release(self) -> None:
        with self._busy_lock:
            self._busy -= 1
        self._slots.release()

    def submit(
        self,
        job: tuple[str, Path, Path],
        name: str,
        params: dict[str, list[str]],
        config_text: str | None,
    ) -> None:
        """Encola un trabajo asíncrono ya admitido (con su hueco reservado)."""
        self.store.update(job[0], state="pending", phase="queued", fraction=0.0)
        self._jobs.put((job, name, params, config_text))

    def _work(self) -> None:
        while True:
            job, name, params, config_text = self._jobs.get()
            try:
                run_job_async(self.store, self.lock, job, name, params, config_text)
            finally:
                self.store.finish(job[0])
                self.release()
                self.store.prune()  # al terminar, no solo al crear: la retención no crece
                self._jobs.task_done()


def _failed(store: JobStore, job_id: str, status: HTTPStatus, problem: str, fix: str) -> None:
    store.finish(job_id)
    store.update(
        job_id, state="failed", phase="failed", error=problem, fix=fix, http_status=int(status)
    )


def _convert_job(
    job_id: str,
    job_dir: Path,
    source: Path,
    name: str,
    params: dict[str, list[str]],
    config_text: str | None,
    on_phase: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    config_path = job_dir / "config.yaml"
    generated = config_text is None
    if generated:
        if on_phase is not None:
            on_phase("draft_config")
        with tempfile.TemporaryDirectory(prefix="extrude-") as workdir:
            config_text = draft_config(read_dxf(ensure_dxf(source, Path(workdir))), Path(name).name)
    if config_text is None:  # draft_config siempre devuelve texto; para el tipado
        raise RequestError(HTTPStatus.INTERNAL_SERVER_ERROR, "Sin configuración.", "Reintenta.")
    config_path.write_text(config_text, encoding="utf-8")
    config = load_config(config_path)  # también traduce errores YAML con línea y columna

    tiles = _number(params, "tiles")
    compress_raw = params.get("compress", [""])[0]
    compression = Compression(compress_raw) if compress_raw else None
    options = ConvertOptions(config, job_dir / "out", tiles, compression, on_phase=on_phase)
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
            "objects": f"{base}/objects.json",
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
    if not math.isfinite(value) or value <= 0:
        raise RequestError(
            HTTPStatus.BAD_REQUEST, f"'{key}' debe ser finito y > 0.", "Ej.: tiles=500"
        )
    return value


def make_handler(
    settings: ServerSettings, store: JobStore, jobs: ConversionQueue | None = None
) -> type[BaseHTTPRequestHandler]:
    work = jobs or ConversionQueue(store)

    class Handler(BaseHTTPRequestHandler):
        server_version = f"extrude/{__version__}"
        timeout = REQUEST_TIMEOUT_S  # StreamRequestHandler: settimeout() del socket

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

        def _bytes(self, data: bytes, content_type: str) -> None:
            self.send_response(HTTPStatus.OK)
            self._cors()
            self.send_header("Content-Type", content_type)
            # Los GLB y planes de un trabajo cambian al confirmar: sin caché
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_body(self, limit: int, what: str) -> bytes:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                raise RequestError(HTTPStatus.BAD_REQUEST, "Cuerpo vacío.", f"Envía {what}.")
            if length > limit:
                self._discard(length)
                raise RequestError(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    f"El cuerpo supera el límite de {limit // 1_048_576} MB.",
                    "Arranca el servidor con --max-mb mayor.",
                )
            data = self.rfile.read(length)
            if len(data) != length:
                raise RequestError(
                    HTTPStatus.BAD_REQUEST, "El cuerpo llegó incompleto.", "Reintenta el envío."
                )
            return data

        def _origin_refused(self, content_type: str) -> bool:
            """Misma regla que la subida de planos: origen permitido y un tipo que obliga a la
            comprobación previa CORS (application/octet-stream o application/json)."""
            origin = self.headers.get("Origin")
            if origin is None:
                return False
            sent = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if origin in settings.allowed_origins and sent == content_type:
                return False
            self.close_connection = True
            if origin not in settings.allowed_origins:
                self._error(
                    HTTPStatus.FORBIDDEN,
                    "Origen no permitido.",
                    "Abre el visor desde un origen permitido (--origin).",
                )
            else:
                self._error(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    f"Envía el cuerpo como {content_type}.",
                    "Usa el visor de Extrude.",
                )
            return True

        def _post_materials(self, path: str, params: dict[str, list[str]]) -> None:
            """POST /api/models y POST /api/jobs/<id>/materials."""
            try:
                if path == "/api/models":
                    if self._origin_refused(UPLOAD_CONTENT_TYPE):
                        return
                    data = self._read_body(settings.max_bytes, "el GLB base")
                    revision = inspect_base(data)
                    job_id, job_dir = store.create()
                    try:
                        out = job_dir / "out"
                        out.mkdir()
                        (out / BASE_GLB).write_bytes(data)
                    except BaseException:
                        store.discard(job_id)
                        raise
                    store.finish(job_id)
                    self._json(
                        HTTPStatus.CREATED,
                        {
                            "job": job_id,
                            "geometryRevision": revision,
                            "files": {"glb": f"/api/jobs/{job_id}/{BASE_GLB}"},
                        },
                    )
                    return
                match = MATERIALS_PATH.match(path)
                if match is None:
                    raise RequestError(HTTPStatus.NOT_FOUND, "Ruta desconocida.", "Revisa la URL.")
                if self._origin_refused(PLAN_CONTENT_TYPE):
                    return
                job_id = match.group(1)
                job_dir = settings.jobs_dir / job_id
                if not (job_dir / "out").is_dir() or store.is_active(job_id):
                    raise RequestError(
                        HTTPStatus.NOT_FOUND if not job_dir.is_dir() else HTTPStatus.CONFLICT,
                        "El trabajo no existe o aún se está convirtiendo.",
                        "Espera a que termine o vuelve a convertir el plano.",
                    )
                body = self._read_body(MAX_PLAN_BODY, "materials.json")
                preview = params.get("preview", [""])[0].lower() in ("1", "true", "yes")
                result = apply_plan(job_dir / "out", body, preview=preview)
                result["url"] = f"/api/jobs/{job_id}/{result['file']}"
                if result["plan"]:
                    result["planUrl"] = f"/api/jobs/{job_id}/{result['plan']}"
                self._json(HTTPStatus.OK, result)
            except TimeoutError:
                self.close_connection = True
            except RequestError as exc:
                self._error(exc.status, exc.problem, exc.fix)
            except PlanRejected as exc:
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": exc.problem, "fix": exc.fix, "issues": exc.issues},
                )
            except ExtrudeError as exc:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.problem, exc.fix)
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc), "Revisa la petición.")
            except Exception as exc:  # respuesta JSON en vez de cortar la conexión
                self.log_error("Error inesperado: %r", exc)
                self._error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    f"Error inesperado al materializar: {exc}",
                    "Revisa la consola de extrude serve.",
                )

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

        def _busy(self) -> None:
            """503 sin procesar el cuerpo: el cliente debe reintentar más tarde."""
            self.close_connection = True
            body = json.dumps(
                {
                    "error": "El servidor ya tiene el máximo de conversiones en curso y en cola.",
                    "fix": f"Espera a que terminen y reintenta (en {BUSY_RETRY_AFTER_S} s).",
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(HTTPStatus.SERVICE_UNAVAILABLE)
            self._cors()
            self.send_header("Retry-After", str(BUSY_RETRY_AFTER_S))
            self.send_header("Connection", "close")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # --- rutas --------------------------------------------------------
        def _forbidden_host(self) -> bool:
            """DNS rebinding: un dominio ajeno que resuelve a 127.0.0.1 llega con su Host."""
            if not settings.loopback_only or host_name(self.headers.get("Host")) in LOOPBACK_HOSTS:
                return False
            self._error(
                HTTPStatus.MISDIRECTED_REQUEST,
                "Host no permitido para un servidor local.",
                "Usa http://127.0.0.1 o http://localhost.",
            )
            return True

        def _browser_upload_refused(self) -> HTTPStatus | None:
            """Un navegador siempre envía Origin: si no está permitido, o no envía el tipo del
            cliente oficial (que obliga a la comprobación previa CORS), no se convierte nada.
            Sin Origin (curl, scripts) no aplica."""
            origin = self.headers.get("Origin")
            if origin is None:
                return None
            if origin not in settings.allowed_origins:
                return HTTPStatus.FORBIDDEN
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type != UPLOAD_CONTENT_TYPE:
                return HTTPStatus.UNSUPPORTED_MEDIA_TYPE
            return None

        def do_OPTIONS(self) -> None:
            if self._forbidden_host():
                return
            origin = self.headers.get("Origin")
            if origin is not None and origin not in settings.allowed_origins:
                self.send_response(HTTPStatus.FORBIDDEN)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:
            if self._forbidden_host():
                return
            parts = urlparse(self.path).path.strip("/").split("/")
            if parts == ["api", "health"]:
                converters = [
                    {"name": s.name, "available": s.available} for s in converter_statuses()
                ]
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "version": __version__,
                        "converters": converters,
                        "queue": {"in_use": work.in_use, "capacity": work.capacity},
                    },
                )
                return
            if parts == ["api", "library"]:
                self._json(HTTPStatus.OK, library_payload())
                return
            texture_match = LIBRARY_TEXTURE.match(urlparse(self.path).path)
            if texture_match:
                png = texture_png(texture_match.group(1))
                if png is None:
                    self._error(HTTPStatus.NOT_FOUND, "Textura desconocida.", "Usa /api/library.")
                    return
                self._bytes(png, "image/png")
                return
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "status":
                status = store.status(parts[2])
                if status is None:
                    self._error(
                        HTTPStatus.NOT_FOUND,
                        "No hay ningún trabajo asíncrono con ese id.",
                        "Repite la conversión con POST /api/convert?async=1.",
                    )
                else:
                    self._json(HTTPStatus.OK, status)
                return
            if len(parts) >= 4 and parts[:2] == ["api", "jobs"]:
                found = store.file(parts[2], "/".join(parts[3:]))
                if found is None:
                    self._error(
                        HTTPStatus.NOT_FOUND,
                        "No existe ese archivo de trabajo.",
                        "Repite la conversión.",
                    )
                    return
                path, content_type = found
                self._bytes(path.read_bytes(), content_type)
                return
            self._error(
                HTTPStatus.NOT_FOUND, "Ruta desconocida.", "Usa /api/health o /api/convert."
            )

        def do_POST(self) -> None:
            url = urlparse(self.path)
            if url.path == "/api/models" or MATERIALS_PATH.match(url.path):
                if self._forbidden_host():
                    self.close_connection = True
                    return
                self._post_materials(url.path, parse_qs(url.query))
                return
            if url.path != "/api/convert":
                self._error(HTTPStatus.NOT_FOUND, "Ruta desconocida.", "Usa POST /api/convert.")
                return
            params = parse_qs(url.query)
            reserved = False
            if self._forbidden_host():
                self.close_connection = True
                return
            refused = self._browser_upload_refused()
            if refused is not None:
                # Sin leer el cuerpo ni crear nada; se cierra para no dejar el cuerpo a medias
                self.close_connection = True
                if refused is HTTPStatus.FORBIDDEN:
                    self._error(
                        refused,
                        "Origen no permitido para convertir planos.",
                        "Abre el visor desde un origen permitido (--origin) o usa extrude convert.",
                    )
                else:
                    self._error(
                        refused,
                        f"Los navegadores deben enviar el plano como {UPLOAD_CONTENT_TYPE}.",
                        "Usa el visor de Extrude o envía Content-Type: application/octet-stream.",
                    )
                return
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
                is_async = params.get("async", [""])[0].lower() in ("1", "true", "yes")
                validate_params(params)  # errores de parámetros antes de leer o convertir
                # Admisión ANTES de leer el cuerpo: lleno → 503 sin ocupar memoria ni disco
                reserved = work.reserve()
                if not reserved:
                    self._busy()
                    return
                data = self.rfile.read(length)
                if len(data) != length:
                    raise RequestError(
                        HTTPStatus.BAD_REQUEST,
                        "El cuerpo llegó incompleto.",
                        "Reintenta el envío del plano.",
                    )
                config_text = _decode_config(self.headers.get(CONFIG_HEADER))
                name = params.get("name", ["plano.dxf"])[0]
                if is_async:
                    job = start_job(store, data, name)
                    work.submit(job, name, params, config_text)
                    reserved = False  # el hueco ya es del trabajador: lo libera al terminar
                    self._json(
                        HTTPStatus.ACCEPTED,
                        {"job": job[0], "state": "pending", "status": f"/api/jobs/{job[0]}/status"},
                    )
                    return
                with work.lock:
                    payload = run_job(store, data, name, params, config_text)
                self._json(HTTPStatus.OK, payload)
            except TimeoutError:
                # El cliente dejó de enviar: no hay a quién responder; se cierra la conexión
                self.log_error("Tiempo de espera agotado leyendo la petición")
                self.close_connection = True
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
            finally:
                if reserved:  # síncrono terminado o rechazado tras reservar
                    work.release()
                    store.prune()

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
    loopback = host_name(host) in LOOPBACK_HOSTS
    settings = ServerSettings(root, max_mb * 1_048_576, allowed_origins, loopback_only=loopback)
    store = JobStore(root)
    return ThreadingHTTPServer((host, port), make_handler(settings, store, ConversionQueue(store)))
