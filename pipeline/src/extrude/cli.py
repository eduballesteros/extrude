"""CLI de Extrude."""

from __future__ import annotations

import typer

from extrude.converters import MANUAL_EXPORT_HINT, converter_statuses

app = typer.Typer(
    help="Extrude: convierte DWG/DXF urbanos en modelos 3D glTF.",
    no_args_is_help=True,
    add_completion=False,
)


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
