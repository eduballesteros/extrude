"""Interfaz común de los conversores DWG→DXF y utilidades de ejecución segura.

El DWG es entrada no confiable: nunca se pasa su nombre original a un proceso externo.
Se copia a un directorio aislado con un nombre fijo y el proceso se lanza sin shell,
con lista de argumentos y timeout.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from extrude.errors import ConversionFailedError, InputFileError

CONVERSION_TIMEOUT_S = 600
STAGED_INPUT_NAME = "input.dwg"
STAGED_OUTPUT_NAME = "input.dxf"
_DWG_MAGIC = re.compile(rb"^AC\d{4}")


@dataclass(frozen=True)
class ConverterStatus:
    name: str
    available: bool
    location: str | None
    hint: str


class DwgConverter(ABC):
    """Convierte un DWG a DXF usando una herramienta externa."""

    name: str
    env_var: str
    install_hint: str

    @abstractmethod
    def candidate_paths(self) -> list[Path]:
        """Rutas típicas de instalación, además de PATH y la variable de entorno."""

    @abstractmethod
    def build_command(self, executable: Path, in_dir: Path, out_dir: Path) -> list[str]:
        """Argumentos del proceso; la entrada está en in_dir/STAGED_INPUT_NAME."""

    def executable_names(self) -> list[str]:
        return [self.name]

    def locate(self) -> Path | None:
        override = os.environ.get(self.env_var)
        if override:
            path = Path(override)
            return path if path.is_file() else None
        for exe in self.executable_names():
            found = shutil.which(exe)
            if found:
                return Path(found)
        return next((p for p in self.candidate_paths() if p.is_file()), None)

    def status(self) -> ConverterStatus:
        location = self.locate()
        return ConverterStatus(
            name=self.name,
            available=location is not None,
            location=str(location) if location else None,
            hint=self.install_hint,
        )

    def convert(self, source: Path, workdir: Path) -> Path:
        executable = self.locate()
        if executable is None:
            raise ConversionFailedError(f"{self.name} no está disponible.", self.install_hint)
        in_dir, out_dir = stage_input(source, workdir)
        command = self.build_command(executable, in_dir, out_dir)
        result = run_tool(command, CONVERSION_TIMEOUT_S)
        output = out_dir / STAGED_OUTPUT_NAME
        if not output.is_file():
            raise ConversionFailedError(
                f"{self.name} no generó el DXF (código {result.returncode}). "
                f"Salida: {(result.stderr or result.stdout).strip()[:500]}",
                "Abre el DWG en AutoCAD/BricsCAD, ejecuta AUDIT y RECOVER, y vuelve a "
                "intentarlo; o expórtalo tú a DXF (SAVEAS → DXF 2018).",
            )
        return output


def validate_dwg(source: Path) -> None:
    if not source.is_file():
        raise InputFileError(
            f"No existe el archivo '{source}'.", "Comprueba la ruta y vuelve a ejecutar."
        )
    with source.open("rb") as fh:
        header = fh.read(6)
    if not _DWG_MAGIC.match(header):
        raise InputFileError(
            f"'{source.name}' no parece un DWG válido (cabecera {header!r}).",
            "Verifica que el archivo no esté dañado o que no sea otro formato renombrado.",
        )


def stage_input(source: Path, workdir: Path) -> tuple[Path, Path]:
    """Copia el DWG a workdir/in/input.dwg y prepara workdir/out."""
    validate_dwg(source)
    in_dir = workdir / "in"
    out_dir = workdir / "out"
    in_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, in_dir / STAGED_INPUT_NAME)
    return in_dir, out_dir


def run_tool(command: list[str], timeout_s: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command, capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise ConversionFailedError(
            f"La conversión superó el límite de {timeout_s} s.",
            "Divide el dibujo (WBLOCK) o purga el DWG (PURGE, -PURGE regapps) y reintenta.",
        ) from exc
