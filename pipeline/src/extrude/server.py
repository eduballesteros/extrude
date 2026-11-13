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
  GET  /api/ai/providers               → proveedores de IA (sin claves), límites y tarifas
  POST /api/jobs/<id>/ai/context       → JSON de solicitud; devuelve lo que se enviaría (sin
                                         llamar a la IA) para revisarlo antes de autorizar
  POST /api/jobs/<id>/ai/propose       → una única llamada al proveedor elegido (o caché);
                                         devuelve una propuesta validada, nunca un plan aplicado
  POST /api/jobs/<id>/ai/cache/clear   → borra la caché de propuestas del trabajo
  GET  /api/jobs/<id>/result           → resultado de la conversión (para reabrir el proyecto)
  GET  /api/jobs/<id>/layers-config    → altura por capa según el YAML asociado al proyecto
  GET  /api/jobs/<id>/review           → revisión del proyecto: apoyo por capa, capas pendientes
                                         de clasificar, terreno usado y si se puede deshacer
  POST /api/jobs/<id>/reconvert        → {heights?, adjust?: {capa: {height?, support?, kind?}}}:
                                         actualiza el YAML y reconvierte el plano guardado; el
                                         resultado anterior solo se sustituye si termina bien y
                                         queda guardado para poder recuperarlo
  POST /api/jobs/<id>/restore          → recupera el resultado anterior a la última reconversión
  GET  /api/projects                   → proyectos convertidos que conserva el servidor
  GET  /api/images/providers           → proveedores de generación de imágenes (sin claves)
  POST /api/images/plan                → qué resolución se pediría (estándar, HD, 4K); sin coste
  POST /api/images/suggest             → Claude propone una redacción del prompt (no genera)
  POST /api/images/generate            → 202: una llamada al proveedor en segundo plano
  GET  /api/images/jobs/<id>[/file]    → estado de la generación / archivo recibido
  POST /api/images/jobs/<id>/cancel    → deja de esperar y descarta el resultado

Seguridad: el archivo subido se guarda con nombre fijo (solo se usa su extensión), con límite
de tamaño; los ids de trabajo y los nombres de archivo se validan con una lista blanca (sin
rutas arbitrarias: nada de `..`, rutas absolutas ni otras subcarpetas); CORS solo para los
orígenes indicados; una conversión a la vez (CPU intensiva); se conservan los últimos
`MAX_JOBS` trabajos.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import json
import math
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

from extrude import __version__, build_info
from extrude.access import ACCESS_FILE, SharedMode, has_role, required_role
from extrude.ai_planner import AiError, AiService, parse_request
from extrude.config import EXTRUDED_KINDS, load_config, parse_config
from extrude.convert import ConvertOptions, convert
from extrude.converters import converter_statuses, ensure_dxf
from extrude.dxf_reader import read_dxf
from extrude.edit_tools import tools_payload
from extrude.errors import ExtrudeError
from extrude.http_server import BoundedHTTPServer
from extrude.image_gen import ImageError, ImageService, parse_references, suggest_prompt
from extrude.init_config import draft_config
from extrude.layer_heights import (
    apply_adjustments,
    base_mode,
    current_adjustments,
    current_heights,
    describe,
)
from extrude.master import (
    MASTER_GLB,
    decompress_to_master,
    export_compressed,
    is_compressed_glb,
)
from extrude.materials_service import (
    BASE_GLB,
    PlanRejected,
    StaleRevision,
    apply_plan,
    inspect_base,
    library_payload,
    project_status,
    step,
    texture_png,
)
from extrude.node_tools import Compression
from extrude.project_edit import apply_edit, parse_apply
from extrude.project_state import LOCK

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
    "model.current.glb": "model/gltf-binary",
    "model.master.glb": "model/gltf-binary",
}
JOB_PATH = re.compile(r"^/api/jobs/([0-9a-f]{32})(?:/(.*))?$")
EXPORT_PATH = re.compile(r"^/api/jobs/([0-9a-f]{32})/export/(draco|meshopt)$")
PLAN_CONTENT_TYPE = "application/json"
MATERIALS_PATH = re.compile(r"^/api/jobs/([0-9a-f]{32})/materials$")
AI_PATH = re.compile(
    r"^/api/jobs/([0-9a-f]{32})/(ai/context|ai/propose|ai/cache/clear|edit/apply|project/undo"
    r"|project/redo)$"
)
MAX_AI_BODY = 2 * 1024 * 1024
MAX_IMAGE_BODY = 72 * 1024 * 1024  # hasta 4 capturas de 12 MB en Base64
IMAGE_JOB_PATH = re.compile(r"^/api/images/jobs/([0-9a-f]{32})(/file|/cancel)?$")
JOB_META = "job.json"
JOB_RESULT = "result.json"
# Resultado anterior a la última reconversión (un nivel), para poder recuperarlo
PREVIOUS_OUT = "out.prev"
PREVIOUS_CONFIG = "config.prev.yaml"
PREVIOUS_RESULT = "result.prev.json"
MAX_LISTED_PROJECTS = 12
LIBRARY_TEXTURE = re.compile(r"^/api/library/([a-z]{1,32})\.png$")
# Los INSERT pueden quedar fuera del bbox geométrico: los índices de convert._write_tiles
# son enteros con signo, sin límite de seis dígitos. Solo se admiten dígitos ASCII.
TILE_FILE = re.compile(r"tiles/tile_-?[0-9]+_-?[0-9]+(?:\.rev-[0-9]{3,})?\.glb")
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


def log(message: str) -> None:
    """Registro del servidor que nunca lanza: una consola que no codifica un carácter, o una
    salida cerrada, no puede tumbar el hilo que registra (ver ConversionQueue._work)."""
    stream = sys.stderr or sys.stdout
    if stream is None:
        return
    try:
        stream.write(message + "\n")
        stream.flush()
    except UnicodeEncodeError:
        try:
            stream.write(message.encode("ascii", "backslashreplace").decode("ascii") + "\n")
            stream.flush()
        except (OSError, ValueError):
            pass
    except (OSError, ValueError):
        pass  # salida cerrada o redirigida a una tubería rota


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
    # Nombres de Host aceptados aunque se escuche en todas las interfaces (--allowed-host)
    allowed_hosts: frozenset[str] = frozenset()
    # Detrás de un proxy de confianza (Caddy): la IP del cliente es la última de X-Forwarded-For
    trust_forwarded: bool = False
    # Modo multiusuario (--shared): cuentas, sesiones, permisos y límites (access.py)
    shared: SharedMode | None = field(default=None, compare=False)


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
        self.on_create: Callable[[str], None] | None = None

    def update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            status = self._statuses.setdefault(job_id, JobStatus())
            for key, value in changes.items():
                setattr(status, key, value)

    def status(self, job_id: str, *, with_result: bool = True) -> dict[str, Any] | None:
        """Estado de un trabajo asíncrono. El resultado de uno terminado no se guarda en
        memoria (puede ocupar cientos de kB por el informe): se lee de su result.json."""
        if not JOB_ID.match(job_id):
            return None
        with self._lock:
            status = self._statuses.get(job_id)
            payload = status.as_dict(job_id) if status else None
        if payload and with_result and payload["state"] == "done" and "result" not in payload:
            try:
                path = self.root / job_id / JOB_RESULT
                payload["result"] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload.update(
                    state="failed",
                    error="El resultado de la conversión ya no está en el servidor.",
                    fix="Vuelve a convertir el plano.",
                    http_status=int(HTTPStatus.GONE),
                )
        return payload

    def create(self) -> tuple[str, Path]:
        """Crea la carpeta y la registra como activa de forma atómica respecto a la poda."""
        job_id = uuid.uuid4().hex
        path = self.root / job_id
        with self._lock:
            path.mkdir()
            self._active.add(job_id)
            if self.on_create is not None:
                self.on_create(job_id)  # modo compartido: dueño = usuario de la petición
        self.prune()
        return job_id, path

    def finish(self, job_id: str) -> None:
        """El trabajo terminó (bien o mal): desde ahora cuenta para la retención."""
        with self._lock:
            self._active.discard(job_id)

    def reactivate(self, job_id: str) -> None:
        """Un trabajo terminado vuelve a convertirse (reconversión con otra configuración)."""
        with self._lock:
            self._active.add(job_id)

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
        Los proyectos con propietario son persistentes: su cuota limita las altas, no los borra.

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
                        if p.is_dir()
                        and JOB_ID.match(p.name)
                        and p.name not in active
                        and not (p / ACCESS_FILE).is_file()
                    ),
                    key=lambda p: p.stat().st_mtime,
                )
            for old in finished[: max(len(finished) - keep, 0)]:
                shutil.rmtree(old, ignore_errors=True)
                with self._lock:
                    self._statuses.pop(old.name, None)


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
    replace: bool = False,
) -> dict[str, Any]:
    try:
        return _convert_job(job_id, job_dir, source, name, params, config_text, on_phase, replace)
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
    replace: bool = False,
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
            result = finish_job(
                job_id, job_dir, source, name, params, config_text, on_phase, replace
            )
        # Primero deja de estar activo y luego se publica: quien vea «done» y envíe otro trabajo
        # encuentra este ya contado como terminado por la poda. El resultado ya está en disco
        # (result.json): en memoria solo queda el estado (ver JobStore.status).
        saved = job_dir / JOB_RESULT
        if not saved.is_file():  # la conversión normal ya lo escribe; se garantiza aquí
            saved.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        store.finish(job_id)
        del result
        store.update(job_id, state="done", phase="done", fraction=1.0, result=None)
    except RequestError as exc:
        _failed(store, job_id, exc.status, exc.problem, exc.fix)
    except ExtrudeError as exc:
        _failed(store, job_id, HTTPStatus.UNPROCESSABLE_ENTITY, exc.problem, exc.fix)
    except ValueError as exc:
        _failed(store, job_id, HTTPStatus.BAD_REQUEST, str(exc), "Revisa los parámetros.")
    except Exception as exc:  # el hilo no debe morir en silencio: el visor espera un estado
        log(f"[extrude serve] Error inesperado en {job_id}: {exc!r}")
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
            tuple[tuple[str, Path, Path], str, dict[str, list[str]], str | None, bool]
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
        replace: bool = False,
    ) -> None:
        """Encola un trabajo asíncrono ya admitido (con su hueco reservado). `replace`: es una
        reconversión del mismo trabajo; su resultado sustituye al anterior solo si termina bien."""
        self.store.update(
            job[0], state="pending", phase="queued", fraction=0.0, result=None, error=None
        )
        self._jobs.put((job, name, params, config_text, replace))

    def _work(self) -> None:
        """Único hilo de conversión: nada de lo que pase en un trabajo puede terminarlo, o
        todos los siguientes quedarían en cola para siempre."""
        while True:
            job, name, params, config_text, replace = self._jobs.get()
            try:
                run_job_async(self.store, self.lock, job, name, params, config_text, replace)
            except BaseException as exc:
                log(f"[extrude serve] Fallo interno en {job[0]}: {exc!r}")
            finally:
                try:
                    status = self.store.status(job[0], with_result=False) or {}
                    if status.get("state") in ACTIVE_STATES:  # nunca «en curso» para siempre
                        _failed(
                            self.store,
                            job[0],
                            HTTPStatus.INTERNAL_SERVER_ERROR,
                            "Error interno al convertir.",
                            "Revisa el registro de extrude serve y vuelve a intentarlo.",
                        )
                    self.store.finish(job[0])
                    self.store.prune()  # al terminar, no solo al crear: la retención no crece
                except BaseException as exc:
                    log(f"[extrude serve] Fallo al cerrar {job[0]}: {exc!r}")
                finally:
                    self.release()
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
    replace: bool = False,
) -> dict[str, Any]:
    """Convierte el plano del trabajo. Con `replace` (reconversión), todo se genera aparte
    (`out.next`, `config.next.yaml`) y solo sustituye al resultado anterior si termina bien."""
    config_path = job_dir / ("config.next.yaml" if replace else "config.yaml")
    out_dir = job_dir / ("out.next" if replace else "out")
    generated = config_text is None
    if generated:
        if on_phase is not None:
            on_phase("draft_config")
        with tempfile.TemporaryDirectory(prefix="extrude-") as workdir:
            config_text = draft_config(read_dxf(ensure_dxf(source, Path(workdir))), Path(name).name)
    if config_text is None:  # draft_config siempre devuelve texto; para el tipado
        raise RequestError(HTTPStatus.INTERNAL_SERVER_ERROR, "Sin configuración.", "Reintenta.")
    tiles = _number(params, "tiles")
    compress_raw = params.get("compress", [""])[0]
    compression = Compression(compress_raw) if compress_raw else None
    try:
        if replace:
            shutil.rmtree(out_dir, ignore_errors=True)
        config_path.write_text(config_text, encoding="utf-8")
        config = load_config(config_path)  # también traduce errores YAML con línea y columna
        options = ConvertOptions(config, out_dir, tiles, compression, on_phase=on_phase)
        result = convert(source, options)
    except BaseException:
        if replace:  # el resultado anterior queda intacto
            shutil.rmtree(out_dir, ignore_errors=True)
            config_path.unlink(missing_ok=True)
        raise
    base = f"/api/jobs/{job_id}"
    payload = {
        "job": job_id,
        "source": name,
        "config_generated": generated,
        "files": {
            "glb": f"{base}/model.glb" if (out_dir / "model.glb").is_file() else None,
            "manifest": f"{base}/manifest.json" if tiles else None,
            "report_md": f"{base}/report.md",
            "report_json": f"{base}/report.json",
            "objects": f"{base}/objects.json",
            "config": f"{base}/config.yaml",
        },
        "report": result.report,
        # El visor comprueba que el modelo que carga es de esta misma revisión
        "revision": result.report.get("geometry_revision"),
        "build": build_info.LOADED[:12],
    }
    if replace:
        # Bajo el candado del proyecto: nadie lee ni escribe `out/` durante el relevo. El
        # resultado anterior (modelo, materiales y ediciones incluidos) se guarda para poder
        # recuperarlo; solo se conserva uno.
        with LOCK:
            previous = job_dir / PREVIOUS_OUT
            shutil.rmtree(previous, ignore_errors=True)
            (job_dir / "out").rename(previous)
            out_dir.rename(job_dir / "out")
            (job_dir / "config.yaml").replace(job_dir / PREVIOUS_CONFIG)
            config_path.replace(job_dir / "config.yaml")
            old_result = job_dir / JOB_RESULT
            if old_result.is_file():
                old_result.replace(job_dir / PREVIOUS_RESULT)
            else:
                (job_dir / PREVIOUS_RESULT).unlink(missing_ok=True)
    meta = {"name": name, "tiles": tiles, "compress": compress_raw or None}
    (job_dir / JOB_META).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (job_dir / JOB_RESULT).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def job_source(job_dir: Path) -> Path | None:
    """Plano original guardado con el trabajo (input.dwg / input.dxf), si lo hay."""
    return next((p for s in sorted(ALLOWED_INPUTS) if (p := job_dir / f"input{s}").is_file()), None)


def job_meta(job_dir: Path) -> dict[str, Any] | None:
    """Nombre y opciones con que se convirtió; para trabajos antiguos, del informe."""
    try:
        meta: dict[str, Any] = json.loads((job_dir / JOB_META).read_text(encoding="utf-8"))
        return meta
    except (OSError, ValueError):
        pass
    try:
        report = json.loads((job_dir / "out" / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    source = job_source(job_dir)
    output = report.get("output", {})
    return {
        # Los proyectos antiguos no guardaron el nombre del plano: uno estable por proyecto,
        # para que sus vistas e imágenes (guardadas por nombre) no cambien al reconvertir
        "name": f"plano-{job_dir.name[:6]}{source.suffix if source else '.dxf'}",
        "tiles": output.get("tile_size"),
        "compress": output.get("compression"),
    }


def job_result(job_dir: Path, job_id: str) -> dict[str, Any] | None:
    """Resultado de la última conversión. Los proyectos convertidos antes de guardarlo se
    reconstruyen de su informe, para poder abrirlos y repararlos desde el visor."""
    try:
        saved: dict[str, Any] = json.loads((job_dir / JOB_RESULT).read_text(encoding="utf-8"))
        return saved
    except (OSError, ValueError):
        pass
    out = job_dir / "out"
    try:
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    meta = job_meta(job_dir) or {}
    base = f"/api/jobs/{job_id}"
    return {
        "job": job_id,
        "source": meta.get("name") or "plano.dxf",
        "config_generated": _looks_generated(job_dir),
        "files": {
            "glb": f"{base}/model.glb" if (out / "model.glb").is_file() else None,
            "manifest": f"{base}/manifest.json" if (out / "manifest.json").is_file() else None,
            "report_md": f"{base}/report.md",
            "report_json": f"{base}/report.json",
            "objects": f"{base}/objects.json",
            "config": f"{base}/config.yaml",
        },
        "report": report,
        "revision": report.get("geometry_revision"),
        "build": None,  # convertido por una ejecución que no dejó su identidad
    }


def _looks_generated(job_dir: Path) -> bool:
    try:
        head = (job_dir / "config.yaml").read_text(encoding="utf-8")[:200]
    except OSError:
        return False
    return "Borrador generado por" in head


def list_projects(jobs_dir: Path, visible: Callable[[str], bool]) -> list[dict[str, Any]]:
    """Proyectos convertidos que conserva el servidor, del más reciente al más antiguo."""
    found: list[tuple[float, dict[str, Any]]] = []
    for job_dir in jobs_dir.iterdir():
        report_path = job_dir / "out" / "report.json"
        if not JOB_ID.match(job_dir.name) or not report_path.is_file():
            continue
        if job_source(job_dir) is None or not visible(job_dir.name):
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        meta = job_meta(job_dir) or {}
        name = Path(str(meta.get("name") or "plano")).stem
        ground: dict[str, int] = {}
        for info in report.get("layers", {}).values():
            for key, value in (info.get("ground_check") or {}).items():
                ground[key] = ground.get(key, 0) + int(value)
        modified = report_path.stat().st_mtime
        found.append(
            (
                modified,
                {
                    "job": job_dir.name,
                    "name": name,
                    "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(modified)),
                    "revision": report.get("geometry_revision"),
                    "floating": ground.get("floating", 0),
                    "buried": ground.get("buried", 0),
                    # Informe anterior a la revisión de apoyo y capas pendientes
                    "outdated": "summary" not in report,
                },
            )
        )
    found.sort(key=lambda item: item[0], reverse=True)
    return [entry for _, entry in found[:MAX_LISTED_PROJECTS]]


def project_review(job_dir: Path, job_id: str) -> dict[str, Any]:
    """Qué hay que revisar en un proyecto: apoyo por capa, capas pendientes de clasificar,
    alturas provisionales y zócalos altos. Vale para proyectos nuevos y para los antiguos."""
    report = json.loads((job_dir / "out" / "report.json").read_text(encoding="utf-8"))
    text = (job_dir / "config.yaml").read_text(encoding="utf-8")
    config = parse_config(yaml.safe_load(text) or {}, source="config.yaml")
    layers = report.get("layers", {})
    terrain = report.get("terrain")
    if terrain is None:
        # Informe anterior: solo hay comprobación de apoyo si existía un terreno triangulable
        checked = any(i.get("ground_check") for i in layers.values() if isinstance(i, dict))
        terrain = {"status": "valid" if checked else "unknown", "reason": None}
    valid = terrain.get("status") == "valid"
    support: list[dict[str, Any]] = []
    for name, info in sorted(layers.items()):
        rule = config.rule_for(name)
        if info.get("status") != "converted" or rule is None or rule.kind not in EXTRUDED_KINDS:
            continue
        check = info.get("ground_check") or {}
        mode = base_mode(rule)
        problems = check.get("floating", 0) + check.get("buried", 0) + check.get("partial_gap", 0)
        if mode in ("auto", "terrain"):
            note = "Se apoya en el terreno."
        elif not valid:
            note = "No hay terreno utilizable en el proyecto: no se puede apoyar ni comprobar."
        elif mode == "elevation":
            note = "Su base es la cota Z de cada huella (regla explícita)." + (
                f" {problems} objetos no coinciden con el terreno." if problems else ""
            )
        elif problems:
            note = (
                f"Su base está fija en una cota y {problems} objetos flotan o quedan "
                "enterrados respecto al terreno."
            )
            if rule.height_from is not None and rule.height_from.value == "elevation":
                note += (
                    " Su altura se dedujo de la cota Z de las huellas; al apoyarla en el "
                    "terreno pasará a ser provisional, como en una importación nueva."
                )
        else:
            note = "Su base está fija en una cota; no se detectan problemas con el terreno."
        support.append(
            {
                "layer": name,
                "kind": rule.kind.value,
                "baseMode": mode,
                "objects": info.get("objects", 0),
                "checked": check.get("checked", 0),
                "floating": check.get("floating", 0),
                "buried": check.get("buried", 0),
                "partialGap": check.get("partial_gap", 0),
                "intersecting": check.get("intersecting", 0),
                "partiallyBuried": check.get("partially_buried", 0),
                "extrapolated": (info.get("terrain_coverage") or {}).get("extrapolated", 0),
                "foundations": info.get("terrain_foundations", 0),
                "highFoundations": info.get("high_foundations", 0),
                "maxFoundationM": info.get("max_foundation_height_m", 0.0),
                "provisionalHeights": (info.get("heights") or {}).get("default_height", 0)
                if rule.kind.value == "building"
                else 0,
                # Se puede pasar al apoyo automático (la política de las importaciones nuevas)
                "canRepair": valid and mode in ("fixed", "elevation"),
                # Solo se propone cuando la base no la eligió el usuario de forma explícita
                "recommended": valid and mode == "fixed" and problems > 0,
                "note": note,
            }
        )
    return {
        "job": job_id,
        "revision": report.get("geometry_revision"),
        "repeatable": job_source(job_dir) is not None,
        "terrain": terrain,
        "summary": report.get("summary"),
        "support": support,
        # None: informe de una versión anterior, que no analizaba las capas ignoradas
        "pending": report.get("pending_layers"),
        "outdated": "summary" not in report,
        "adjustments": current_adjustments(text),
        "canRestore": (job_dir / PREVIOUS_OUT).is_dir(),
        "configGenerated": _looks_generated(job_dir),
        "build": build_info.status(public=True),
    }


def restore_previous(job_dir: Path) -> None:
    """Intercambia el resultado actual con el anterior a la última reconversión (deshacer; se
    puede volver a deshacer). Modelo, informe, materiales, ediciones y YAML vuelven juntos."""
    previous = job_dir / PREVIOUS_OUT
    if not previous.is_dir():
        raise ExtrudeError(
            "No hay un resultado anterior que recuperar.", "Solo se guarda el de la última vez."
        )
    with LOCK:
        swap = job_dir / "out.swap"
        shutil.rmtree(swap, ignore_errors=True)
        (job_dir / "out").rename(swap)
        previous.rename(job_dir / "out")
        swap.rename(previous)
        for current, old in (("config.yaml", PREVIOUS_CONFIG), (JOB_RESULT, PREVIOUS_RESULT)):
            a, b, tmp = job_dir / current, job_dir / old, job_dir / f"{current}.swap"
            if a.is_file():
                a.replace(tmp)
            if b.is_file():
                b.replace(a)
            if tmp.is_file():
                tmp.replace(b)


def layers_config(job_dir: Path, job_id: str) -> dict[str, Any]:
    """Altura por capa del proyecto y si se puede reconvertir (y si no, por qué)."""
    config_path = job_dir / "config.yaml"
    source = job_source(job_dir)
    try:
        report = json.loads((job_dir / "out" / "report.json").read_text(encoding="utf-8"))
        text = config_path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return {
            "repeatable": False,
            "reason": "Este modelo no procede de un plano convertido en el servidor (se abrió "
            "un GLB): no hay plano ni configuración con que repetir la conversión.",
            "layers": [],
            "heights": {},
            "config": None,
        }
    names = [n for n, info in report.get("layers", {}).items() if isinstance(info, dict)]
    return {
        "repeatable": source is not None,
        "reason": None
        if source is not None
        else "El servidor ya no conserva el plano original de este proyecto.",
        "layers": [entry.as_dict() for entry in describe(text, names)],
        "heights": current_heights(text),
        "config": f"/api/jobs/{job_id}/config.yaml",
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
    settings: ServerSettings,
    store: JobStore,
    jobs: ConversionQueue | None = None,
    ai: AiService | None = None,
    images: ImageService | None = None,
) -> type[BaseHTTPRequestHandler]:
    work = jobs or ConversionQueue(store)
    assistant = ai or AiService()
    gallery = images or ImageService(settings.jobs_dir / "_images")

    class Handler(BaseHTTPRequestHandler):
        server_version = f"extrude/{__version__}"
        timeout = REQUEST_TIMEOUT_S  # StreamRequestHandler: settimeout() del socket

        def log_message(self, format: str, *args: Any) -> None:
            log(f"[extrude serve] {self.address_string()} {format % args}")

        def client_ip(self) -> str:
            """IP del cliente. Detrás de Caddy (--trust-forwarded) la conexión llega del proxy:
            se usa la última entrada de X-Forwarded-For, la que añade el propio proxy."""
            if settings.trust_forwarded:
                forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[-1].strip()
                if forwarded:
                    return forwarded
            return str(self.client_address[0])

        # --- utilidades ---------------------------------------------------
        def _cors(self) -> None:
            origin = self.headers.get("Origin")
            if origin and origin in settings.allowed_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header(
                    "Access-Control-Allow-Headers", f"Content-Type, Authorization, {CONFIG_HEADER}"
                )
                self.send_header("Access-Control-Allow-Private-Network", "true")

        def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _export(self, job_id: str, method: str) -> None:
            """GET /api/jobs/<id>/export/<draco|meshopt>: GLB comprimido del estado actual."""
            out = settings.jobs_dir / job_id / "out"
            if store.is_active(job_id) or not out.is_dir():
                self._error(HTTPStatus.NOT_FOUND, "Trabajo desconocido.", "Revisa el id.")
                return
            try:
                with LOCK:
                    status = project_status(out, job_id)
                    name = Path(urlparse(status["modelUrl"]).path).name
                    path = export_compressed(out, out / name, method, status["revision"])
                    data = path.read_bytes()
            except ExtrudeError as exc:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.problem, exc.fix)
                return
            self._bytes(data, "model/gltf-binary")

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
                    compressed = is_compressed_glb(data)
                    revision = None if compressed else inspect_base(data)
                    job_id, job_dir = store.create()
                    try:
                        out = job_dir / "out"
                        out.mkdir()
                        (out / BASE_GLB).write_bytes(data)
                        if compressed:
                            # Draco/Meshopt: se decodifica a un maestro editable (master.py)
                            master = decompress_to_master(out)
                            revision = inspect_base(master.read_bytes())
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
                base = params.get("base", [None])[0]
                if base is not None and not re.fullmatch(r"rev-[0-9]{3,}", base):
                    raise RequestError(HTTPStatus.BAD_REQUEST, "base no válido.", "rev-NNN")
                if settings.shared and not preview and base is None:
                    raise RequestError(
                        HTTPStatus.PRECONDITION_REQUIRED,
                        "En un servidor compartido la confirmación lleva la revisión base.",
                        "Actualiza el visor (envía ?base=rev-NNN).",
                    )
                try:
                    result = apply_plan(job_dir / "out", body, preview=preview, expect=base)
                except StaleRevision as exc:
                    self._json(
                        HTTPStatus.CONFLICT,
                        {
                            "error": exc.problem,
                            "fix": "Recarga el proyecto: otra persona confirmó un cambio.",
                            "kind": "stale_revision",
                            "revision": exc.current,
                        },
                    )
                    return
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

        def _post_ai(self, match: re.Match[str]) -> None:
            """Fase 3: propuesta (como mucho una llamada al proveedor), aplicar un plan y
            deshacer/rehacer revisiones. Aplicar y deshacer nunca llaman a la IA."""
            if self._origin_refused(PLAN_CONTENT_TYPE):
                return
            job_id, action = match.group(1), match.group(2)
            job_dir = settings.jobs_dir / job_id
            try:
                if not (job_dir / "out").is_dir() or store.is_active(job_id):
                    raise RequestError(
                        HTTPStatus.NOT_FOUND if not job_dir.is_dir() else HTTPStatus.CONFLICT,
                        "El trabajo no existe o aún se está convirtiendo.",
                        "Espera a que termine o vuelve a convertir el plano.",
                    )
                body = self._read_body(MAX_AI_BODY, "la solicitud en JSON")
                if action in ("project/undo", "project/redo"):
                    expect = parse_qs(urlparse(self.path).query).get("expect", [None])[0]
                    if expect is not None and not re.fullmatch(r"rev-[0-9]{3,}", expect):
                        raise RequestError(HTTPStatus.BAD_REQUEST, "expect no válido.", "rev-NNN")
                    if settings.shared and expect is None:
                        raise RequestError(
                            HTTPStatus.PRECONDITION_REQUIRED,
                            "En un servidor compartido deshacer/rehacer lleva la revisión actual.",
                            "Actualiza el visor (envía ?expect=rev-NNN).",
                        )
                    try:
                        state = step(
                            job_dir / "out", job_id, forward=action.endswith("redo"), expect=expect
                        )
                    except StaleRevision as exc:
                        raise AiError(
                            409, "stale_revision", exc.problem, exc.fix, revision=exc.current
                        ) from None
                    self._json(HTTPStatus.OK, state)
                    return
                if action == "edit/apply":
                    preview = parse_qs(urlparse(self.path).query).get("preview", [""])[0] == "1"
                    result = apply_edit(job_dir, parse_apply(body), preview=preview)
                    self._json(HTTPStatus.OK, result)
                    return
                action = action.removeprefix("ai/")
                if action == "cache/clear":
                    removed = assistant.clear_cache(job_dir)
                    self._json(HTTPStatus.OK, {"removed": removed})
                    return
                request = parse_request(body)
                if action == "context":
                    self._json(HTTPStatus.OK, assistant.context_preview(job_id, job_dir, request))
                else:
                    self._json(HTTPStatus.OK, assistant.propose(job_id, job_dir, request))
            except TimeoutError:
                self.close_connection = True
            except ConnectionError:  # el visor canceló o cerró la espera
                pass  # el visor canceló la espera: la respuesta tardía se descarta
            except RequestError as exc:
                self._error(exc.status, exc.problem, exc.fix)
            except AiError as exc:
                payload: dict[str, Any] = {"error": exc.problem, "fix": exc.fix, "kind": exc.kind}
                payload.update(exc.extra)
                with contextlib.suppress(ConnectionError):
                    self._json(HTTPStatus(exc.status), payload)
            except ExtrudeError as exc:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.problem, exc.fix)
            except Exception as exc:  # respuesta JSON en vez de cortar la conexión
                self.log_error("Error inesperado en IA: %r", exc)
                self._error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "Error inesperado al preparar la propuesta.",
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
            """DNS rebinding: un dominio ajeno que resuelve a 127.0.0.1 llega con su Host.
            Con --allowed-host se exige uno de esos nombres aunque se escuche en 0.0.0.0 (el
            Docker local); escuchando en loopback, los nombres locales."""
            host = host_name(self.headers.get("Host"))
            if settings.allowed_hosts:
                if host in settings.allowed_hosts:
                    return False
            elif not settings.loopback_only or host in LOOPBACK_HOSTS:
                return False
            self._error(
                HTTPStatus.MISDIRECTED_REQUEST,
                "Host no permitido para este servidor.",
                "Abre Extrude con su dirección habitual (p. ej. http://localhost).",
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

        def _bearer(self) -> str | None:
            value = self.headers.get("Authorization") or ""
            return value[7:].strip() if value.lower().startswith("bearer ") else None

        def _gate(self, method: str) -> bool:
            """Modo compartido: sesión válida, permiso sobre el trabajo y límites. True = ya
            respondido (rechazado). En modo local no hace nada."""
            shared = settings.shared
            if shared is None:
                return False
            path = urlparse(self.path).path
            shared.current_user = None
            if path in ("/api/health", "/api/auth/login", "/api/auth/me"):
                return False
            user = shared.sessions.user(self._bearer())
            if user is None:
                self.close_connection = True  # no se lee un cuerpo que no se va a usar
                self._json(
                    HTTPStatus.UNAUTHORIZED,
                    {
                        "error": "Inicia sesión para usar este servidor compartido.",
                        "fix": "Entra con tu usuario en el visor.",
                        "kind": "auth_required",
                    },
                )
                return True
            shared.current_user = user
            job = JOB_PATH.match(path)
            if job:
                rest = (job.group(2) or "").split("/")
                role = shared.access.role(job.group(1), user)
                if not has_role(role, required_role(method, rest)):
                    self.close_connection = True
                    # Sin permiso = como si no existiera: no se revela qué trabajos hay
                    self._error(
                        HTTPStatus.NOT_FOUND if role is None else HTTPStatus.FORBIDDEN,
                        "No existe ese trabajo." if role is None else "Tu rol no permite cambios.",
                        "Pide acceso a su dueño." if role is None else "Pide el rol editor.",
                    )
                    return True
            creating = method == "POST" and path in ("/api/convert", "/api/models")
            if creating and shared.access.owned_by(user) >= shared.limits.max_jobs_per_user:
                self.close_connection = True
                self._error(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    f"Has llegado al máximo de {shared.limits.max_jobs_per_user} trabajos.",
                    "Pide al administrador que libere trabajos antiguos.",
                )
                return True
            asking = method == "POST" and (
                path.endswith("/ai/propose")
                or path in ("/api/images/generate", "/api/images/suggest")
            )
            if asking and not shared.ai_calls.allow(user):
                self.close_connection = True
                self._error(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    f"Máximo de {shared.limits.max_ai_per_hour} consultas de IA por hora.",
                    "Espera un rato antes de volver a consultar.",
                )
                return True
            return False

        def _auth_post(self, path: str) -> None:
            """POST /api/auth/login|logout y /api/jobs/<id>/access."""
            shared = settings.shared
            if shared is None:
                self._error(HTTPStatus.NOT_FOUND, "Servidor local sin cuentas.", "No hace falta.")
                return
            try:
                body = json.loads(self._read_body(16 * 1024, "la petición") or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("JSON no es un objeto")
            except (ValueError, RequestError):
                self._error(HTTPStatus.BAD_REQUEST, "Petición no válida.", "Envía JSON.")
                return
            if path == "/api/auth/login":
                if self._origin_refused("application/json"):
                    return
                user, password = str(body.get("user", "")), str(body.get("password", ""))
                try:
                    token = shared.login(user, password, self.client_ip())
                except ExtrudeError as exc:
                    self._error(HTTPStatus.TOO_MANY_REQUESTS, exc.problem, exc.fix)
                    return
                if token is None:
                    self._error(
                        HTTPStatus.UNAUTHORIZED, "Usuario o contraseña incorrectos.", "Revísalos."
                    )
                    return
                self._json(
                    HTTPStatus.OK,
                    {"token": token, "user": user, "expiresInS": int(shared.sessions.ttl_s)},
                )
                return
            if path == "/api/auth/logout":
                shared.sessions.revoke(self._bearer())
                self._json(HTTPStatus.OK, {"ok": True})
                return
            job = JOB_PATH.match(path)
            if job and job.group(2) == "access":
                target = str(body.get("user", ""))
                if target not in shared.accounts.names():
                    self._error(HTTPStatus.NOT_FOUND, f"No existe el usuario '{target}'.", "")
                    return
                try:
                    data = shared.access.share(job.group(1), target, body.get("role"))
                except ExtrudeError as exc:
                    self._error(HTTPStatus.BAD_REQUEST, exc.problem, exc.fix)
                    return
                self._json(HTTPStatus.OK, {"owner": data["owner"], "members": data["members"]})
                return
            self._error(HTTPStatus.NOT_FOUND, "Ruta desconocida.", "Revisa la URL.")

        # --- proyecto: resultado, altura por capa y reconversión -------------
        def _job_info(self, job_id: str, what: str) -> None:
            job_dir = settings.jobs_dir / job_id
            if not job_dir.is_dir():
                self._error(HTTPStatus.NOT_FOUND, "Ese proyecto ya no está en el servidor.", "")
                return
            if what == "layers-config":
                try:
                    with LOCK:
                        payload = layers_config(job_dir, job_id)
                except ExtrudeError as exc:
                    self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.problem, exc.fix)
                    return
                payload["busy"] = store.is_active(job_id)
                self._json(HTTPStatus.OK, payload)
                return
            if what == "review":
                try:
                    with LOCK:
                        payload = project_review(job_dir, job_id)
                except (OSError, ValueError):
                    self._error(
                        HTTPStatus.NOT_FOUND,
                        "Este proyecto no tiene informe de conversión.",
                        "Vuelve a convertir el plano.",
                    )
                    return
                except ExtrudeError as exc:
                    self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.problem, exc.fix)
                    return
                payload["busy"] = store.is_active(job_id)
                self._json(HTTPStatus.OK, payload)
                return
            with LOCK:
                result = job_result(job_dir, job_id)
            if result is None or store.is_active(job_id):
                self._error(
                    HTTPStatus.NOT_FOUND,
                    "Este proyecto no tiene un resultado de conversión disponible.",
                    "Vuelve a convertir el plano.",
                )
                return
            self._json(HTTPStatus.OK, result)

        def _restore(self, job_id: str) -> None:
            """POST /api/jobs/<id>/restore: vuelve al resultado anterior a la reconversión."""
            if self._json_body(1024) is None:
                return
            job_dir = settings.jobs_dir / job_id
            if not job_dir.is_dir() or store.is_active(job_id):
                self._error(HTTPStatus.CONFLICT, "El proyecto no está disponible.", "Reintenta.")
                return
            try:
                restore_previous(job_dir)
                with LOCK:
                    result = job_result(job_dir, job_id)
            except ExtrudeError as exc:
                self._error(HTTPStatus.CONFLICT, exc.problem, exc.fix)
                return
            self._json(HTTPStatus.OK, result or {})

        def _json_body(self, limit: int) -> dict[str, Any] | None:
            """Cuerpo JSON (objeto) de una petición del visor; None si ya se respondió."""
            if self._origin_refused("application/json"):
                return None
            try:
                body = json.loads(self._read_body(limit, "la petición en JSON"))
                if not isinstance(body, dict):
                    raise ValueError("JSON no es un objeto")
            except RequestError as exc:
                self._error(exc.status, exc.problem, exc.fix)
                return None
            except ValueError:
                self._error(HTTPStatus.BAD_REQUEST, "Petición no válida.", "Envía JSON.")
                return None
            return body

        @staticmethod
        def _align_with_new_imports(job_dir: Path, changes: dict[str, dict[str, Any]]) -> None:
            """Reparar el apoyo de un borrador generado aplica la política de una importación
            nueva: con terreno, la cota Z de una huella ya no se toma como altura. Las reglas de
            una configuración escrita por el usuario no se tocan."""
            if not _looks_generated(job_dir):
                return
            try:
                text = (job_dir / "config.yaml").read_text(encoding="utf-8")
                config = parse_config(yaml.safe_load(text) or {}, source="config.yaml")
            except (OSError, ExtrudeError, yaml.YAMLError):
                return
            for layer, change in changes.items():
                rule = config.rule_for(layer)
                if (
                    change.get("support") == "auto"
                    and rule is not None
                    and rule.height_from is not None
                    and rule.height_from.value == "elevation"
                ):
                    change.setdefault("z_height", "ignore")

        def _reconvert(self, job_id: str) -> None:
            """POST /api/jobs/<id>/reconvert: nuevas alturas por capa → YAML → conversión."""
            body = self._json_body(64 * 1024)
            if body is None:
                return
            job_dir = settings.jobs_dir / job_id
            source = job_source(job_dir) if job_dir.is_dir() else None
            meta = job_meta(job_dir) if source else None
            if source is None or meta is None or not (job_dir / "config.yaml").is_file():
                self._json(
                    HTTPStatus.CONFLICT,
                    {
                        "error": "Este proyecto no se puede volver a convertir.",
                        "fix": "Solo los planos convertidos en este servidor conservan su "
                        "DWG/DXF. Sube el plano de nuevo.",
                        "kind": "not_repeatable",
                    },
                )
                return
            heights = body.get("heights", {})
            adjust = body.get("adjust", {})
            valid = all(
                isinstance(group, dict) and all(isinstance(k, str) for k in group)
                for group in (heights, adjust)
            )
            if not valid or not (heights or adjust):
                self._error(
                    HTTPStatus.BAD_REQUEST, "Faltan los cambios.", "Envía {heights} o {adjust}."
                )
                return
            # Un único conjunto de ajustes por capa: altura, apoyo y clasificación
            changes: dict[str, dict[str, Any]] = {
                layer: dict(value) if isinstance(value, dict) else {"kind": value}
                for layer, value in adjust.items()
            }
            for layer, value in heights.items():
                changes.setdefault(layer, {})["height"] = value
            self._align_with_new_imports(job_dir, changes)
            if store.is_active(job_id):
                self._json(
                    HTTPStatus.CONFLICT,
                    {
                        "error": "Este proyecto ya se está convirtiendo.",
                        "fix": "Espera a que termine.",
                        "kind": "busy",
                    },
                )
                return
            try:
                current = (job_dir / "config.yaml").read_text(encoding="utf-8")
                config_text = apply_adjustments(current, changes)
            except ExtrudeError as exc:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.problem, exc.fix)
                return
            if not work.reserve():
                self._busy()
                return
            params: dict[str, list[str]] = {}
            if meta.get("tiles"):
                params["tiles"] = [str(meta["tiles"])]
            if meta.get("compress"):
                params["compress"] = [str(meta["compress"])]
            store.reactivate(job_id)
            work.submit((job_id, job_dir, source), str(meta["name"]), params, config_text, True)
            self._json(
                HTTPStatus.ACCEPTED,
                {"job": job_id, "state": "pending", "status": f"/api/jobs/{job_id}/status"},
            )

        # --- generación de imágenes ------------------------------------------
        def _image_owner(self) -> str | None:
            return settings.shared.current_user if settings.shared else None

        def _image_error(self, exc: ImageError) -> None:
            self._json(
                HTTPStatus(exc.status), {"error": exc.problem, "fix": exc.fix, "kind": exc.kind}
            )

        def _image_get(self, job_id: str, tail: str | None) -> None:
            try:
                if tail == "/file":
                    data, mime = gallery.file(job_id, self._image_owner())
                    self._bytes(data, mime)
                elif tail is None:
                    self._json(HTTPStatus.OK, gallery.get(job_id, self._image_owner()).as_dict())
                else:
                    self._error(HTTPStatus.NOT_FOUND, "Ruta desconocida.", "Revisa la URL.")
            except ImageError as exc:
                self._image_error(exc)

        def _image_post(self, path: str) -> None:
            cancel = IMAGE_JOB_PATH.match(path)
            limit = MAX_IMAGE_BODY if path.endswith(("/generate", "/suggest")) else 64 * 1024
            body = self._json_body(limit)
            if body is None:
                return
            try:
                if cancel and cancel.group(2) == "/cancel":
                    self._json(HTTPStatus.OK, gallery.cancel(cancel.group(1), self._image_owner()))
                elif path == "/api/images/plan":
                    self._json(HTTPStatus.OK, gallery.plan(body))
                elif path == "/api/images/generate":
                    self._json(HTTPStatus.ACCEPTED, gallery.start(body, self._image_owner()))
                elif path == "/api/images/suggest":
                    prompt = body.get("prompt")
                    text = suggest_prompt(
                        prompt if isinstance(prompt, str) else "",
                        parse_references(body.get("images")),
                    )
                    self._json(HTTPStatus.OK, {"suggestion": text})
                else:
                    self._error(HTTPStatus.NOT_FOUND, "Ruta desconocida.", "Revisa la URL.")
            except ImageError as exc:
                self._image_error(exc)

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
            if self._forbidden_host() or self._gate("GET"):
                return
            parts = urlparse(self.path).path.strip("/").split("/")
            if parts == ["api", "auth", "me"]:
                shared = settings.shared
                user = shared.sessions.user(self._bearer()) if shared else None
                self._json(
                    HTTPStatus.OK,
                    {"mode": "shared" if shared else "local", "user": user},
                )
                return
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "access":
                data = settings.shared.access.read(parts[2]) if settings.shared else None
                if data is None:
                    self._error(HTTPStatus.NOT_FOUND, "Sin permisos (modo local).", "")
                    return
                self._json(HTTPStatus.OK, {"owner": data["owner"], "members": data["members"]})
                return
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
                        # Qué código ejecuta este proceso y si coincide con el del disco
                        "build": build_info.status(public=settings.shared is not None),
                    },
                )
                return
            if parts == ["api", "library"]:
                self._json(HTTPStatus.OK, library_payload())
                return
            if parts == ["api", "projects"]:
                shared = settings.shared
                user = shared.current_user if shared else None

                def visible(job: str) -> bool:
                    return shared is None or (
                        user is not None and shared.access.role(job, user) is not None
                    )

                self._json(HTTPStatus.OK, {"projects": list_projects(settings.jobs_dir, visible)})
                return
            if parts == ["api", "images", "providers"]:
                self._json(HTTPStatus.OK, gallery.providers_payload())
                return
            image_job = IMAGE_JOB_PATH.match(urlparse(self.path).path)
            if image_job:
                self._image_get(image_job.group(1), image_job.group(2))
                return
            if (
                len(parts) == 4
                and parts[:2] == ["api", "jobs"]
                and parts[3] in ("result", "layers-config", "review")
                and JOB_ID.match(parts[2])
            ):
                self._job_info(parts[2], parts[3])
                return
            if parts == ["api", "tools"]:
                self._json(HTTPStatus.OK, tools_payload())
                return
            if (
                len(parts) == 4
                and parts[:2] == ["api", "jobs"]
                and parts[3] == "project"
                and re.fullmatch(r"[0-9a-f]{32}", parts[2])
            ):
                out = settings.jobs_dir / parts[2] / "out"
                has_model = (out / BASE_GLB).is_file() or (out / MASTER_GLB).is_file()
                if not has_model or store.is_active(parts[2]):
                    self._error(HTTPStatus.NOT_FOUND, "Proyecto sin modelo base.", "Convierte.")
                    return
                try:
                    self._json(HTTPStatus.OK, project_status(out, parts[2]))
                except ExtrudeError as exc:
                    self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.problem, exc.fix)
                return
            export = EXPORT_PATH.match(urlparse(self.path).path)
            if export:
                self._export(export.group(1), export.group(2))
                return
            if parts == ["api", "ai", "providers"]:
                self._json(HTTPStatus.OK, assistant.providers_payload())
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
                with LOCK:
                    found = store.file(parts[2], "/".join(parts[3:]))
                    if found is None:
                        self._error(
                            HTTPStatus.NOT_FOUND,
                            "No existe ese archivo de trabajo.",
                            "Repite la conversión.",
                        )
                        return
                    path, content_type = found
                    file_bytes = path.read_bytes()
                self._bytes(file_bytes, content_type)
                return
            self._error(
                HTTPStatus.NOT_FOUND, "Ruta desconocida.", "Usa /api/health o /api/convert."
            )

        def do_POST(self) -> None:
            url = urlparse(self.path)
            if self._forbidden_host() or self._gate("POST"):
                self.close_connection = True
                return
            if url.path.startswith("/api/auth/") or url.path.endswith("/access"):
                self._auth_post(url.path)
                return
            if url.path.startswith("/api/images/"):
                self._image_post(url.path)
                return
            reconvert = JOB_PATH.match(url.path)
            if reconvert and reconvert.group(2) == "reconvert":
                self._reconvert(reconvert.group(1))
                return
            if reconvert and reconvert.group(2) == "restore":
                self._restore(reconvert.group(1))
                return
            ai_match = AI_PATH.match(url.path)
            if ai_match:
                if self._forbidden_host():
                    self.close_connection = True
                    return
                self._post_ai(ai_match)
                return
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
    ai: AiService | None = None,
    shared: SharedMode | None = None,
    images: ImageService | None = None,
    allowed_hosts: tuple[str, ...] = (),
    trust_forwarded: bool = False,
) -> ThreadingHTTPServer:
    root = jobs_dir or Path(tempfile.gettempdir()) / "extrude-jobs"
    loopback = host_name(host) in LOOPBACK_HOSTS
    settings = ServerSettings(
        root,
        max_mb * 1_048_576,
        allowed_origins,
        loopback_only=loopback,
        allowed_hosts=frozenset(host_name(h) for h in allowed_hosts if h.strip()),
        trust_forwarded=trust_forwarded,
        shared=shared,
    )
    store = JobStore(root)
    if shared is not None:
        if not shared.accounts.names():
            raise ExtrudeError(
                "El modo compartido necesita al menos un usuario.",
                "Crea uno con `extrude users add NOMBRE --users ARCHIVO`.",
            )

        def owner(job_id: str) -> None:
            user = shared.current_user
            if user is not None:
                shared.access.grant_owner(job_id, user)

        store.on_create = owner
    handler = make_handler(
        settings, store, ConversionQueue(store), ai, images or ImageService(root / "_images")
    )
    try:
        return BoundedHTTPServer((host, port), handler)
    except OSError as exc:
        raise ExtrudeError(
            f"No se puede escuchar en {host}:{port}: {exc.strerror or exc}.",
            "Probablemente otro `extrude serve` sigue abierto en ese puerto: ciérralo o usa "
            "--port (y abre el visor con ?server=http://127.0.0.1:PUERTO).",
        ) from exc
