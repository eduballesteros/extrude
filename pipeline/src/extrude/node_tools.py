"""Herramientas Node opcionales (carpeta `tools/` del repo): validación y compresión glTF.

Se ejecuta `node` directamente (sin shell ni `npx.cmd`) y con rutas generadas por Extrude.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from enum import StrEnum
from pathlib import Path
from typing import Any

from extrude.errors import ExtrudeError

TOOLS_DIR_ENV = "EXTRUDE_TOOLS_DIR"
NODE_TIMEOUT_S = 900
QUANTIZATION_BITS = 16
QUANTIZE_ARGS = (
    "--quantize-position",
    str(QUANTIZATION_BITS),
    "--quantize-generic",
    str(QUANTIZATION_BITS),
)
INSTALL_HINT = "Instala Node.js ≥ 22 y ejecuta `npm install` en la carpeta tools/ del repositorio."


class Compression(StrEnum):
    DRACO = "draco"
    MESHOPT = "meshopt"


class NodeToolError(ExtrudeError):
    """Falta Node/las herramientas o la herramienta falló."""


def tools_dir() -> Path:
    override = os.environ.get(TOOLS_DIR_ENV)
    return Path(override) if override else Path(__file__).resolve().parents[3] / "tools"


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        raise NodeToolError("No se encuentra `node` en el PATH.", INSTALL_HINT)
    return node


def _script(relative: str) -> Path:
    path = tools_dir() / relative
    if not path.is_file():
        raise NodeToolError(f"No existe {path}.", INSTALL_HINT)
    return path


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [_node(), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=NODE_TIMEOUT_S,
            check=False,
            cwd=tools_dir(),
        )
    except subprocess.TimeoutExpired as exc:
        raise NodeToolError(
            f"La herramienta Node superó {NODE_TIMEOUT_S} s.", "Usa --tiles para dividir la escena."
        ) from exc


def compress(source: Path, target: Path, method: Compression) -> None:
    cli = _script("node_modules/@gltf-transform/cli/bin/cli.js")
    # 16 bits: máximo admitido; los _OBJECT_ID enteros ≤ 65535 se recuperan exactos al redondear.
    result = _run([str(cli), method.value, str(source), str(target), *QUANTIZE_ARGS])
    if result.returncode != 0 or not target.is_file():
        raise NodeToolError(
            f"gltf-transform {method.value} falló: {(result.stderr or result.stdout)[-800:]}",
            "Prueba sin --compress o con el otro método.",
        )


def attributes(path: Path) -> list[dict[str, Any]]:
    """Resumen decodificado (también Draco/Meshopt) de _OBJECT_ID y POSITION por primitiva."""
    result = _run([str(_script("attributes.mjs")), str(path)])
    if result.returncode != 0:
        raise NodeToolError(
            f"No se pudo decodificar {path.name}: {result.stderr[-800:]}", INSTALL_HINT
        )
    parsed: list[dict[str, Any]] = json.loads(result.stdout)
    return parsed


def validate(paths: list[Path]) -> list[dict[str, Any]]:
    """Informe de gltf-validator por archivo (errors, warnings, messages)."""
    result = _run([str(_script("validate.mjs")), *(str(p) for p in paths)])
    try:
        parsed: list[dict[str, Any]] = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise NodeToolError(f"gltf-validator falló: {result.stderr[-800:]}", INSTALL_HINT) from exc
    return parsed
