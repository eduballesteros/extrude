"""Conversión DWG→DXF: elige automáticamente el adaptador disponible.

Orden de preferencia: ODA File Converter (más fiable) → LibreDWG. Un `.dxf` se usa
directamente (passthrough), sin procesos externos.
"""

from __future__ import annotations

from pathlib import Path

from extrude.converters.base import ConverterStatus, DwgConverter
from extrude.converters.libredwg import LibreDwgConverter
from extrude.converters.oda import OdaFileConverter
from extrude.errors import ConverterNotFoundError, InputFileError

MANUAL_EXPORT_HINT = (
    "Exporta el dibujo a DXF desde AutoCAD/BricsCAD (SAVEAS → DXF, versión 2018 o 2013) "
    "y pasa el .dxf directamente."
)


def dwg_converters() -> list[DwgConverter]:
    return [OdaFileConverter(), LibreDwgConverter()]


def converter_statuses() -> list[ConverterStatus]:
    return [c.status() for c in dwg_converters()]


def ensure_dxf(source: Path, workdir: Path) -> Path:
    """Devuelve la ruta de un DXF legible a partir de un .dxf o .dwg."""
    suffix = source.suffix.lower()
    if suffix == ".dxf":
        if not source.is_file():
            raise InputFileError(
                f"No existe el archivo '{source}'.", "Comprueba la ruta y vuelve a ejecutar."
            )
        return source
    if suffix != ".dwg":
        raise InputFileError(
            f"Extensión no soportada: '{source.suffix}'.", "Usa un archivo .dwg o .dxf."
        )
    for converter in dwg_converters():
        if converter.locate() is not None:
            return converter.convert(source, workdir)
    hints = "\n    - ".join(c.install_hint for c in dwg_converters())
    raise ConverterNotFoundError(
        "No hay ningún conversor DWG→DXF instalado.",
        f"Instala uno de estos:\n    - {hints}\n  o bien: {MANUAL_EXPORT_HINT}",
    )


__all__ = ["ConverterStatus", "DwgConverter", "converter_statuses", "dwg_converters", "ensure_dxf"]
