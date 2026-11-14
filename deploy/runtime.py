"""Entrypoint de Compose: secretos en archivo, un único proceso compartido."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

PROVIDER_KEYS = frozenset({"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"})


def load_secrets(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.keys() - PROVIDER_KEYS:
        raise ValueError("Archivo de proveedores inválido: solo se permiten claves de proveedor")
    for name, value in data.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Las claves de proveedor deben ser cadenas no vacías")
        os.environ[name] = value


def main() -> None:
    os.umask(0o077)
    if sys.argv[1:] == ["health"]:
        with urlopen("http://127.0.0.1:8765/api/health", timeout=3) as response:
            if not json.load(response).get("ok"):
                raise SystemExit(1)
        return
    # El CLI permite crear/listar usuarios con la misma identidad y volumen.
    if sys.argv[1:]:
        os.execvp("extrude", ["extrude", *sys.argv[1:]])
    origin = os.environ.get("EXTRUDE_ORIGIN", "")
    parsed = urlparse(origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.username:
        raise ValueError("EXTRUDE_ORIGIN debe ser un origen HTTPS sin ruta ni credenciales")
    load_secrets(Path("/run/secrets/providers"))
    os.execvp(
        "extrude",
        [
            "extrude",
            "serve",
            "--host",
            "0.0.0.0",
            "--port",
            "8765",
            "--shared",
            "--users",
            "/data/accounts/users.json",
            "--jobs-dir",
            "/data/jobs",
            "--origin",
            origin,
            "--max-mb",
            "100",
            # Producción: claves solo del secreto de Compose, nunca de un .env
            "--no-env-file",
            # Solo el dominio publicado (y la comprobación de salud interna) en Host
            "--allowed-host",
            parsed.hostname,
            "--allowed-host",
            "127.0.0.1",
            # La API solo es accesible desde Caddy en la red de Compose: la IP real del
            # cliente viene en X-Forwarded-For (límites de acceso por cliente)
            "--trust-forwarded",
        ],
    )


if __name__ == "__main__":
    main()
