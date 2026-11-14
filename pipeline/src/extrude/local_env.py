"""Claves de IA desde un archivo `.env` local para `extrude serve` en desarrollo.

Es el mismo archivo que usa Docker local (`compose.yaml` en la raíz del repositorio), así las
claves viven en un único sitio. Reglas, para que no sorprenda:

- Se busca `.env` en la carpeta actual y sus padres, hasta la raíz del repositorio (la que
  tiene `.git`); fuera de un repositorio no se busca más arriba que la carpeta actual.
- Solo se cargan las claves de proveedores y las variables `EXTRUDE_*`; el resto se ignora.
- Una variable ya definida en el entorno manda sobre el archivo, y los valores vacíos no
  cuentan (las líneas `CLAVE=` de la plantilla no borran nada).

Producción no lo usa: sus claves llegan como secreto de Compose (`deploy/README.md`).
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path

ENV_FILE = ".env"
PROVIDER_KEYS = frozenset({"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"})


def find_env_file(start: Path) -> Path | None:
    """El `.env` más cercano subiendo desde `start`, sin salir del repositorio."""
    start = start.resolve()
    inside_repo = any((d / ".git").exists() for d in (start, *start.parents))
    for directory in (start, *start.parents):
        candidate = directory / ENV_FILE
        if candidate.is_file():
            return candidate
        if not inside_repo or (directory / ".git").exists():
            return None
    return None


def _accepted(name: str) -> bool:
    return name in PROVIDER_KEYS or name.startswith("EXTRUDE_")


def _value(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw.split(" #", 1)[0].strip()  # comentario al final de la línea


def parse_env(text: str) -> dict[str, str]:
    """Pares `NOMBRE=valor` aceptados; ignora comentarios, líneas vacías y `export`."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, raw = line.partition("=")
        name = name.removeprefix("export ").strip()
        value = _value(raw)
        if _accepted(name) and value:
            values[name] = value
    return values


def load_env_file(path: Path, environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Carga en el entorno las variables de `path` que falten. Devuelve sus nombres (nunca
    los valores, para poder mostrarlos en consola)."""
    env = os.environ if environ is None else environ
    loaded: list[str] = []
    for name, value in parse_env(path.read_text(encoding="utf-8-sig")).items():
        if env.get(name, "").strip():
            continue
        env[name] = value
        loaded.append(name)
    return sorted(loaded)
