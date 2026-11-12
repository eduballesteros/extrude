"""CLI de Extrude."""

from __future__ import annotations

import io
import json
import math
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer

from extrude.catalog import load_catalog
from extrude.config import load_config
from extrude.convert import ConvertOptions
from extrude.convert import convert as run_convert
from extrude.converters import MANUAL_EXPORT_HINT, converter_statuses, ensure_dxf
from extrude.dxf_reader import read_dxf
from extrude.errors import ExtrudeError
from extrude.init_config import draft_config
from extrude.inspection import format_text, summarize
from extrude.material_library import DEFAULT_LIBRARY
from extrude.materiality import load_plan, validate_plan
from extrude.materialize import materialize as run_materialize
from extrude.model import LengthUnit
from extrude.node_tools import Compression
from extrude.node_tools import compress as compress_glb
from extrude.server import DEFAULT_MAX_MB, DEFAULT_ORIGINS, DEFAULT_PORT, create_server

app = typer.Typer(
    help="Extrude: convierte DWG/DXF urbanos en modelos 3D glTF.",
    no_args_is_help=True,
    add_completion=False,
)

USER_ERROR_EXIT = 2
TOLERANT_ERRORS = ("replace", "backslashreplace", "xmlcharrefreplace", "ignore")


@contextmanager
def _user_errors() -> Iterator[None]:
    try:
        yield
    except ExtrudeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(USER_ERROR_EXIT) from exc


def _tolerant_console() -> None:
    """Las consolas de Windows (cp1252) no codifican →, ², etc.: sin esto, `typer.echo` lanza
    UnicodeEncodeError después de convertir y el comando sale con error aunque todo se escribió.
    Se sustituye lo que no se pueda codificar en vez de fallar."""
    for stream in (sys.stdout, sys.stderr):
        # "strict" en consola y "surrogateescape" al redirigir: ambos fallan con →
        if isinstance(stream, io.TextIOWrapper) and stream.errors not in TOLERANT_ERRORS:
            stream.reconfigure(errors="replace")


@app.callback()
def main() -> None:
    """Extrude: convierte DWG/DXF urbanos en modelos 3D glTF."""
    _tolerant_console()


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


@app.command()
def convert(
    source: Annotated[Path, typer.Argument(help="Archivo .dwg o .dxf")],
    config: Annotated[Path, typer.Option("-c", "--config", help="YAML del proyecto")],
    out: Annotated[Path, typer.Option("-o", "--out", help="Carpeta de salida")] = Path("out"),
    tiles: Annotated[
        float | None, typer.Option(help="Tamaño de tesela en metros (un GLB por tesela)")
    ] = None,
    compress: Annotated[
        Compression | None, typer.Option(help="Compresión final con gltf-transform")
    ] = None,
    units: Annotated[
        LengthUnit | None, typer.Option(help="Fuerza las unidades (prevalece sobre el YAML)")
    ] = None,
) -> None:
    """Convierte el dibujo a GLB (metros, Y-up, recentrado) y escribe report.md/json."""
    with _user_errors():
        if tiles is not None and (not math.isfinite(tiles) or tiles <= 0):
            raise ExtrudeError("--tiles debe ser finito y > 0.", "Ejemplo: --tiles 500 (metros).")
        options = ConvertOptions(load_config(config), out, tiles, compress, units)
        result = run_convert(source, options)
    report = result.report
    output = report["output"]
    typer.echo(
        f"OK: {len(output['files'])} GLB · {output['triangles']:,} triángulos · "
        f"{output['total_size_bytes'] / 1_048_576:.2f} MB → {out}"
    )
    for warning in report["warnings"]:
        typer.echo(f"  aviso: {warning}")
    typer.echo(f"Informe: {out / 'report.md'}")


@app.command()
def serve(
    host: Annotated[
        str, typer.Option(help="Interfaz de red (solo local por defecto)")
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Puerto")] = DEFAULT_PORT,
    max_mb: Annotated[int, typer.Option(help="Tamaño máximo del DWG/DXF (MB)")] = DEFAULT_MAX_MB,
    origin: Annotated[
        list[str] | None, typer.Option(help="Origen permitido por CORS (repetible)")
    ] = None,
    jobs_dir: Annotated[Path | None, typer.Option(help="Carpeta de trabajos")] = None,
    ai_simulated: Annotated[
        bool,
        typer.Option(
            "--ai-simulated",
            help="Añade un proveedor de IA simulado (reglas locales, sin red ni coste)",
        ),
    ] = False,
    shared: Annotated[
        bool,
        typer.Option(
            "--shared",
            help="Modo multiusuario: inicio de sesión, permisos por proyecto y límites "
            "(no cambia --host: sigue siendo local salvo que lo indiques)",
        ),
    ] = False,
    users: Annotated[
        Path | None, typer.Option(help="Archivo de usuarios (extrude users add) para --shared")
    ] = None,
) -> None:
    """Servidor local para que el visor convierta DWG/DXF arrastrándolos."""
    from extrude.access import AccessControl, Accounts, SharedMode
    from extrude.ai_planner import AiService, default_providers

    origins = tuple(origin) if origin else DEFAULT_ORIGINS
    ai = AiService(default_providers(simulated=ai_simulated))
    mode = None
    with _user_errors():
        if shared:
            if users is None:
                raise ExtrudeError("--shared necesita --users.", "Ej.: --users usuarios.json")
            root = jobs_dir or Path(tempfile.gettempdir()) / "extrude-jobs"
            root.mkdir(parents=True, exist_ok=True)
            mode = SharedMode(Accounts(users), AccessControl(root))
            jobs_dir = root
        server = create_server(
            host,
            port,
            jobs_dir=jobs_dir,
            max_mb=max_mb,
            allowed_origins=origins,
            ai=ai,
            shared=mode,
        )
    if mode is not None:
        typer.echo(f"Modo compartido: {len(mode.accounts.names())} usuarios; sesión obligatoria.")
    for entry in ai.providers_payload()["providers"]:
        state = "listo" if entry["available"] else f"no disponible: {entry['reason']}"
        typer.echo(f"IA {entry['name']} ({entry['model'] or 'sin modelo'}): {state}")
    typer.echo(f"Extrude escuchando en http://{host}:{port} (orígenes: {', '.join(origins)})")
    typer.echo("Abre el visor y arrastra un .dwg o .dxf. Ctrl+C para salir.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        typer.echo("Servidor detenido.")
    finally:
        server.server_close()


users_app = typer.Typer(help="Usuarios del modo compartido (extrude serve --shared)")
app.add_typer(users_app, name="users")


@users_app.command("add")
def users_add(
    name: Annotated[str, typer.Argument(help="Nombre de usuario (minúsculas, dígitos, . _ -)")],
    users: Annotated[Path, typer.Option(help="Archivo de usuarios")],
    password_stdin: Annotated[
        bool, typer.Option("--password-stdin", help="Lee la contraseña de la entrada estándar")
    ] = False,
) -> None:
    """Crea o cambia un usuario (contraseña con scrypt; nunca se guarda en claro)."""
    import getpass

    from extrude.access import Accounts

    with _user_errors():
        if password_stdin:
            password = sys.stdin.readline().rstrip("\r\n")
        else:
            password = getpass.getpass("Contraseña: ")
            if getpass.getpass("Repite la contraseña: ") != password:
                raise ExtrudeError("Las contraseñas no coinciden.", "Vuelve a intentarlo.")
        Accounts(users).add(name, password)
    typer.echo(f"Usuario '{name}' guardado en {users}.")


@users_app.command("list")
def users_list(users: Annotated[Path, typer.Option(help="Archivo de usuarios")]) -> None:
    """Lista los usuarios (sin datos de contraseña)."""
    from extrude.access import Accounts

    for name in Accounts(users).names():
        typer.echo(name)


@app.command("init-config")
def init_config(
    source: Annotated[Path, typer.Argument(help="Archivo .dwg o .dxf del proyecto")],
    output: Annotated[
        Path | None, typer.Option("-o", "--output", help="YAML de salida (por defecto, stdout)")
    ] = None,
    units: Annotated[LengthUnit | None, typer.Option(help="Fuerza las unidades del dibujo")] = None,
    force: Annotated[bool, typer.Option(help="Sobrescribe el YAML si ya existe")] = False,
) -> None:
    """Genera un borrador de configuración YAML adaptado a las capas de este dibujo."""
    with _user_errors(), tempfile.TemporaryDirectory(prefix="extrude-") as workdir:
        if output is not None and output.exists() and not force:
            raise ExtrudeError(f"'{output}' ya existe.", "Usa otro nombre con -o o añade --force.")
        document = draft_config(
            read_dxf(ensure_dxf(source, Path(workdir)), units_override=units), source.name
        )
        if output is None:
            typer.echo(document)
        else:
            output.write_text(document, encoding="utf-8")
            typer.echo(f"Borrador escrito en {output}. Revísalo antes de convertir.")


@app.command("validate-materials")
def validate_materials(
    plan: Annotated[Path, typer.Argument(help="materials.json a validar")],
    objects: Annotated[
        Path, typer.Option("--objects", help="objects.json generado por extrude convert")
    ],
) -> None:
    """Valida un plan de materialidad contra el catálogo de objetos y la biblioteca."""
    _tolerant_console()
    with _user_errors():
        parsed = load_plan(plan)
        try:
            catalog = load_catalog(objects)
        except (OSError, ValueError) as exc:
            raise ExtrudeError(
                f"No se puede leer el catálogo '{objects}': {exc}.",
                "Usa el objects.json que acompaña al model.glb de la misma conversión.",
            ) from exc
        issues = validate_plan(parsed, catalog)
    for issue in issues:
        where = f" (asignación {issue.assignment + 1})" if issue.assignment is not None else ""
        typer.echo(f"[{issue.code}]{where} {issue.message}", err=True)
    if issues:
        raise typer.Exit(1)
    count = sum(len(a.object_ids) for a in parsed.assignments)
    typer.echo(
        f"Plan válido: {len(parsed.assignments)} asignaciones ({count} objeto·superficie), "
        f"{len(parsed.omissions)} omisiones, revisión {parsed.material_revision}."
    )


@app.command()
def materialize(
    model: Annotated[Path, typer.Argument(help="model.glb sin comprimir de extrude convert")],
    plan: Annotated[Path, typer.Argument(help="materials.json confirmado")],
    output: Annotated[
        Path | None,
        typer.Option(
            "-o", "--output", help="GLB de salida (por defecto <modelo>.materialized.glb)"
        ),
    ] = None,
    compress: Annotated[
        Compression | None, typer.Option(help="Compresión final con gltf-transform")
    ] = None,
) -> None:
    """Aplica un plan de materialidad al GLB: materiales PBR, texturas incorporadas y UV a escala
    física. No vuelve a convertir el dibujo."""
    _tolerant_console()
    target = output or model.with_name(f"{model.stem}.materialized.glb")
    with _user_errors():
        if target.resolve() == model.resolve():
            raise ExtrudeError(
                "La salida sobrescribiría el GLB base.", "Indica otro nombre con -o."
            )
        try:
            data = model.read_bytes()
        except OSError as exc:
            raise ExtrudeError(f"No se puede leer '{model}': {exc}.", "Revisa la ruta.") from exc
        try:
            glb, stats = run_materialize(data, load_plan(plan))
        except ValueError as exc:
            raise ExtrudeError(
                f"'{model.name}' no es un GLB válido: {exc}.", "Usa el model.glb de convert."
            ) from exc
        # Se escribe a un temporal y se renombra: un fallo no deja un GLB a medias (RN-03)
        partial = target.with_name(f"{target.name}.partial")
        partial.write_bytes(glb)
        if compress is not None:
            compressed = target.with_name(f"{target.name}.compressed")
            try:
                compress_glb(partial, compressed, compress)
            finally:
                partial.unlink(missing_ok=True)
            compressed.replace(target)
        else:
            partial.replace(target)
    typer.echo(
        f"OK: {stats.materials} materiales · {stats.textures} texturas · "
        f"{stats.draw_calls} primitivas · {stats.assigned_triangles:,} triángulos asignados · "
        f"{target.stat().st_size / 1_048_576:.2f} MB → {target}"
    )
    if stats.split_instance_nodes:
        typer.echo(f"  {stats.split_instance_nodes} grupos de instancias separados por material")


@app.command()
def library() -> None:
    """Lista los materiales de la biblioteca (IDs y versiones para materials.json)."""
    _tolerant_console()
    typer.echo(f"Biblioteca {DEFAULT_LIBRARY.version}")
    for p in DEFAULT_LIBRARY.presets:
        texture_name = p.pattern or "sin textura"
        typer.echo(
            f"  {p.id:<20} v{p.version}  {p.name:<22} {p.color}  rugosidad {p.roughness:.2f}  "
            f"{p.tile_size_m[0]:g}×{p.tile_size_m[1]:g} m  ({texture_name})"
        )
