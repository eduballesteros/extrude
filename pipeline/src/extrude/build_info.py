"""Identidad de la ejecución: qué código tiene cargado ESTE proceso.

La versión del paquete no distingue dos ejecuciones del mismo repositorio. Un servidor
arrancado antes de un cambio sigue con el código antiguo en memoria aunque los archivos del
disco ya sean otros, y eso no se arregla recargando el visor.

`LOADED` es la huella del código fuente en el momento de importar este módulo (al arrancar).
`status()` la compara con la huella actual del disco: si difieren, el proceso está
desfasado (`stale`) y hay que reiniciarlo para que las correcciones surtan efecto.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any

PACKAGE_DIR = Path(__file__).resolve().parent


def source_fingerprint(root: Path = PACKAGE_DIR) -> str:
    """sha256 del código Python del paquete (rutas relativas y contenido, en orden)."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        try:
            # Sin distinguir finales de línea: el mismo código en CRLF o LF es el mismo código
            digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        except OSError:
            digest.update(b"?")
        digest.update(b"\0")
    return digest.hexdigest()


LOADED = source_fingerprint()
STARTED_AT = time.time()


def status(*, public: bool = False) -> dict[str, Any]:
    """Identidad del proceso y si su código coincide con el del disco. `public`: el
    servidor está expuesto (modo compartido) y `/api/health` no exige sesión: no se
    publica el PID del proceso."""
    current = source_fingerprint()
    info: dict[str, Any] = {
        "id": LOADED[:12],
        "source": current[:12],
        "stale": current != LOADED,
        "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(STARTED_AT)),
    }
    if not public:
        info["pid"] = os.getpid()
    return info
