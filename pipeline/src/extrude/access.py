"""Modo multiusuario de `extrude serve --shared`: cuentas, sesiones, permisos por proyecto y
límites por usuario.

- Cuentas en un archivo JSON (`extrude users add`), contraseñas con scrypt (sal propia por
  usuario); nunca se guardan ni se registran en claro.
- Sesiones: token aleatorio de 256 bits en `Authorization: Bearer …`, caducidad fija y cierre de
  sesión. Viven en memoria (reiniciar el servidor obliga a entrar de nuevo).
- Permisos: `access.json` dentro de cada trabajo (`owner`, `members` {usuario: editor|viewer}).
  Quien no tiene permiso recibe 404, igual que si el trabajo no existiera (aislamiento).
- Concurrencia: toda confirmación lleva la revisión base (`baseRevision` o `?base=`); si otra
  persona confirmó antes, 409 y nada cambia (ver materials_service.StaleRevision).
- Límites: intentos de acceso fallidos por usuario, trabajos por usuario y consultas de IA por
  usuario y hora.

El modo compartido NO cambia la interfaz de red: sigue en 127.0.0.1 salvo `--host` explícito.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from extrude.errors import ExtrudeError

ACCESS_FILE = "access.json"
USER_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
MIN_PASSWORD = 10
SCRYPT = {"n": 2**14, "r": 8, "p": 1}
SESSION_TTL_S = 8 * 3600
Role = Literal["owner", "editor", "viewer"]
RANK = {"viewer": 1, "editor": 2, "owner": 3}


def _hash(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)


class Accounts:
    """Archivo de usuarios: {"users": {nombre: {salt, hash, n, r, p}}}."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schemaVersion": 1, "users": {}}
        data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        return data

    def names(self) -> list[str]:
        return sorted(self._load()["users"])

    def add(self, name: str, password: str) -> None:
        if not USER_NAME.match(name):
            raise ExtrudeError(
                f"Nombre de usuario no válido: '{name}'.",
                "Usa 2-32 caracteres: minúsculas, dígitos, punto, guion o guion bajo.",
            )
        if len(password) < MIN_PASSWORD:
            raise ExtrudeError(
                f"La contraseña debe tener al menos {MIN_PASSWORD} caracteres.",
                "Usa una frase larga.",
            )
        salt = secrets.token_bytes(16)
        entry = {"salt": salt.hex(), "hash": _hash(password, salt, **SCRYPT).hex(), **SCRYPT}
        with self._lock:
            data = self._load()
            data["users"][name] = entry
            self.path.parent.mkdir(parents=True, exist_ok=True)
            partial = self.path.with_suffix(".partial")
            partial.write_text(json.dumps(data, indent=1), encoding="utf-8")
            partial.replace(self.path)

    def verify(self, name: str, password: str) -> bool:
        entry = self._load()["users"].get(name)
        if entry is None:
            # Mismo coste que un usuario real: no se distingue «no existe» por el tiempo
            _hash(password, b"\x00" * 16, **SCRYPT)
            return False
        expected = bytes.fromhex(entry["hash"])
        got = _hash(password, bytes.fromhex(entry["salt"]), entry["n"], entry["r"], entry["p"])
        return hmac.compare_digest(expected, got)


@dataclass
class Sessions:
    ttl_s: float = SESSION_TTL_S
    _tokens: dict[str, tuple[str, float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def create(self, user: str) -> str:
        token = secrets.token_hex(32)
        with self._lock:
            self._tokens[hashlib.sha256(token.encode()).hexdigest()] = (
                user,
                time.monotonic() + self.ttl_s,
            )
        return token

    def user(self, token: str | None) -> str | None:
        if not token:
            return None
        key = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            found = self._tokens.get(key)
            if found is None:
                return None
            if found[1] < time.monotonic():
                del self._tokens[key]
                return None
            return found[0]

    def revoke(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._tokens.pop(hashlib.sha256(token.encode()).hexdigest(), None)


@dataclass
class Limits:
    max_failed_logins: int = 5
    lockout_s: float = 300
    max_jobs_per_user: int = 50
    max_ai_per_hour: int = 30


class RateLimiter:
    """Ventana deslizante por clave (usuario)."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            events = self._events.setdefault(key, deque())
            while events and events[0] < now - self.window_s:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True

    def count(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            events = self._events.get(key, deque())
            return sum(1 for e in events if e >= now - self.window_s)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)


class AccessControl:
    """Permisos por trabajo (access.json en la carpeta del trabajo)."""

    def __init__(self, jobs_dir: Path) -> None:
        self.jobs_dir = jobs_dir
        self._lock = threading.Lock()

    def _path(self, job_id: str) -> Path:
        return self.jobs_dir / job_id / ACCESS_FILE

    def read(self, job_id: str) -> dict[str, Any] | None:
        path = self._path(job_id)
        if not path.is_file():
            return None
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data

    def grant_owner(self, job_id: str, user: str) -> None:
        with self._lock:
            data = {"schemaVersion": 1, "owner": user, "members": {}}
            self._path(job_id).write_text(json.dumps(data), encoding="utf-8")

    def role(self, job_id: str, user: str) -> Role | None:
        data = self.read(job_id)
        if data is None:
            return None  # trabajo sin dueño (creado en modo local): nadie lo ve en compartido
        if data["owner"] == user:
            return "owner"
        role = data.get("members", {}).get(user)
        return role if role in ("editor", "viewer") else None

    def share(self, job_id: str, user: str, role: str | None) -> dict[str, Any]:
        if role not in (None, "editor", "viewer"):
            raise ExtrudeError(f"Rol '{role}' no válido.", "Usa editor, viewer o null.")
        with self._lock:
            data = self.read(job_id)
            if data is None:
                raise ExtrudeError("Trabajo sin permisos.", "Vuelve a crearlo en modo compartido.")
            if user == data["owner"]:
                raise ExtrudeError("No se cambia el rol del dueño.", "Elige otro usuario.")
            members = dict(data.get("members", {}))
            if role is None:
                members.pop(user, None)
            else:
                members[user] = role
            data["members"] = members
            self._path(job_id).write_text(json.dumps(data), encoding="utf-8")
            return data

    def owned_by(self, user: str) -> int:
        count = 0
        for path in self.jobs_dir.glob(f"*/{ACCESS_FILE}"):
            try:
                if json.loads(path.read_text(encoding="utf-8")).get("owner") == user:
                    count += 1
            except (OSError, ValueError):
                continue
        return count


@dataclass
class SharedMode:
    """Todo lo del modo compartido, inyectado en el manejador HTTP."""

    accounts: Accounts
    access: AccessControl
    sessions: Sessions = field(default_factory=Sessions)
    limits: Limits = field(default_factory=Limits)
    failed: RateLimiter = field(init=False)
    ai_calls: RateLimiter = field(init=False)
    local: threading.local = field(default_factory=threading.local)

    def __post_init__(self) -> None:
        self.failed = RateLimiter(self.limits.max_failed_logins, self.limits.lockout_s)
        self.ai_calls = RateLimiter(self.limits.max_ai_per_hour, 3600)

    def login(self, user: str, password: str) -> str | None:
        """Token o None. Tras N fallos seguidos, el usuario queda bloqueado un tiempo (aunque
        después acierte), sin decir si el usuario existe."""
        if self.failed.count(user) >= self.limits.max_failed_logins:
            raise ExtrudeError(
                "Demasiados intentos fallidos.", "Espera unos minutos antes de volver a probar."
            )
        if not self.accounts.verify(user, password):
            self.failed.allow(user)
            return None
        self.failed.reset(user)
        return self.sessions.create(user)

    # Usuario de la petición en curso (para JobStore.create, que no recibe el manejador)
    @property
    def current_user(self) -> str | None:
        return getattr(self.local, "user", None)

    @current_user.setter
    def current_user(self, user: str | None) -> None:
        self.local.user = user


def required_role(method: str, rest: list[str]) -> Role:
    """Rol mínimo para /api/jobs/<id>/<rest…> con este método."""
    if rest[:1] == ["access"] and method == "POST":
        return "owner"
    return "editor" if method == "POST" else "viewer"


def has_role(role: Role | None, needed: Role) -> bool:
    return role is not None and RANK[role] >= RANK[needed]
