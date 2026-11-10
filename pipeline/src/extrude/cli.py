"""CLI de Extrude."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer

from extrude.converters import MANUAL_EXPORT_HINT, converter_statuses, ensure_dxf
from extrude.dxf_reader import read_dxf
from extrude.errors import ExtrudeError
from extrude.inspection import format_text, summarize
from extrude.model import LengthUnit

app = typer.Typer(
    help="Extrude: convierte DWG/DXF urbanos en modelos 3D glTF.",
    no_args_is_help=True,
    add_completion=False,
)

USER_ERROR_EXIT = 2


@contextmanager
def _user_errors() -> Iterator[None]:
    try:
        yield
    except ExtrudeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(USER_ERROR_EXIT) from exc


@app.callback()
def main() -> None:
    """Extrude: convierte DWG/DXF urbanos en modelos 3D glTF."""


@app.command()
def doctor() -> None:
    """Comprueba qué conversores DWG→DXF están disponibles."""
    statuses = converter_statuses()
    typer.echo("Conversores DWG→DXF:")
    for status in statuses:
        mark = "OK " if status.available else "-- "
        detail = status.location if status.available else "no encontrado"
        typer.echo(f"  [{mark}] {status.name}: {detail}")
    typer.echo("  [OK ] DXF directo (passthrough): siempre disponible")
    if not any(s.available for s in statuses):
        typer.echo("\nAviso: solo se pueden procesar archivos .dxf. Para DWG:")
        for status in statuses:
            typer.echo(f"  - {status.hint}")
        typer.echo(f"  - {MANUAL_EXPORT_HINT}")


@app.command()
def inspect(
    source: Annotated[Path, typer.Argument(help="Archivo .dwg o .dxf")],
    units: Annotated[LengthUnit | None, typer.Option(help="Fuerza las unidades del dibujo")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Salida en JSON")] = False,
) -> None:
    """Lista capas, tipos de entidad, bbox y unidades, sin generar 3D."""
    with _user_errors(), tempfile.TemporaryDirectory(prefix="extrude-") as workdir:
        dxf = ensure_dxf(source, Path(workdir))
        summary = summarize(read_dxf(dxf, units_override=units))
        summary.file = str(source)
        if as_json:
            typer.echo(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
        else:
            typer.echo(format_text(summary))
