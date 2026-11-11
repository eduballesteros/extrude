"""Informe de conversión: `report.json` (máquina) y `report.md` (persona).

Nada se descarta en silencio: todo lo leído aparece como convertido, ignorado, sin regla,
sin geometría, no soportado, omitido o fallido, con su capa y su número.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from extrude import __version__
from extrude.heights import HeightOrigin

if TYPE_CHECKING:
    from extrude.convert import ConvertOptions, ConvertResult

REPORT_JSON = "report.json"
REPORT_MD = "report.md"


def write_reports(result: ConvertResult, source: Path, options: ConvertOptions) -> dict[str, Any]:
    report = build_report(result, source, options)
    out = options.out_dir
    (out / REPORT_JSON).write_text(json.dumps(report, indent=2, ensure_ascii=False), "utf-8")
    (out / REPORT_MD).write_text(render_markdown(report), "utf-8")
    return report


def _rows(counter: Counter[tuple[str, str]]) -> list[dict[str, Any]]:
    return [
        {"layer": layer, "type": dxftype, "count": n}
        for (layer, dxftype), n in sorted(counter.items())
    ]


def build_report(result: ConvertResult, source: Path, options: ConvertOptions) -> dict[str, Any]:
    read, build = result.read, result.build
    by_type: Counter[str] = Counter()
    for (_, dxftype), n in read.read.items():
        by_type[dxftype] += n

    layers: dict[str, dict[str, Any]] = {}
    exported = {layer.name: layer for layer in result.layers}
    for name, geometry in build.layers.items():
        stats = geometry.stats
        layer = exported[name]
        layers[name] = {
            "status": "converted",
            "kind": geometry.rule.kind.value,
            "features": dict(stats.features),
            "objects": len(geometry.objects),
            "merged_objects": len(layer.objects),
            "instanced": {g.block: len(g.matrices) for g in layer.groups},
            "lines": len(geometry.lines),
            "triangles": stats.triangles,
            "topology": vars(stats.topology).copy(),
            "heights": {str(k): v for k, v in stats.heights.items()},
            "ambiguous_heights": stats.ambiguous_heights,
            "nearby_text_heights": stats.nearby_text_heights,
            "dropped_without_height": stats.dropped_without_height,
            "degenerate_triangles": stats.degenerate_triangles,
            "ground_check": stats.ground.as_dict() if stats.ground else None,
            "ground_details": stats.ground_details,
            "terrain_coverage": stats.terrain_coverage,
            "terrain_foundations": sum(o.foundation_height > 0 for o in geometry.objects),
            "max_foundation_height_m": max(
                (o.foundation_height for o in geometry.objects), default=0.0
            ),
        }
    for status, group in (
        ("ignored", build.ignored),
        ("unruled", build.unruled),
        ("no_geometry", build.without_geometry),
    ):
        for name, counts in group.items():
            layers[name] = {"status": status, "features": dict(counts)}
    # Capas cuyas entidades no generaron ningún elemento (todas no soportadas/omitidas/fallidas)
    read_by_layer: dict[str, Counter[str]] = {}
    for (name, dxftype), n in read.read.items():
        read_by_layer.setdefault(name, Counter())[dxftype] += n
    for name, counts in read_by_layer.items():
        if name not in layers:
            layers[name] = {"status": "not_convertible", "features": dict(counts)}

    outputs = [
        {
            "path": o.path.relative_to(options.out_dir).as_posix(),
            "size_bytes": o.stats.size_bytes,
            "triangles": o.stats.triangles,
            "draw_calls": o.stats.draw_calls,
            "instanced_blocks": o.stats.instanced_blocks,
            "instances": o.stats.instances,
        }
        for o in result.outputs
    ]
    return {
        "generator": f"extrude {__version__}",
        "source": source.name,
        "dxf_version": read.dxf_version,
        "units": read.units.value,
        "units_assumed": read.units_assumed,
        "origin_offset": list(result.origin),
        "crs": options.config.crs,
        "entities": {
            "read_by_type": dict(by_type.most_common()),
            "unsupported": _rows(read.unsupported),
            "skipped": _rows(read.skipped),
            "failed": _rows(read.failed),
        },
        "layers": dict(sorted(layers.items())),
        "output": {
            "files": outputs,
            "total_size_bytes": sum(o["size_bytes"] for o in outputs),
            "triangles": sum(o["triangles"] for o in outputs),
            "draw_calls": sum(o["draw_calls"] for o in outputs),
            "compression": options.compression.value if options.compression else None,
            "tile_size": options.tile_size,
        },
        "timings_s": result.timings,
        "warnings": _warnings(result, layers),
    }


def _warnings(result: ConvertResult, layers: dict[str, dict[str, Any]]) -> list[str]:
    read, build = result.read, result.build
    warnings = list(read.warnings) + list(build.warnings)
    if read.unsupported:
        detail = ", ".join(
            f"{t}×{n} en {layer}" for (layer, t), n in sorted(read.unsupported.items())
        )
        warnings.append(
            f"Entidades ACIS no convertidas ({detail}). En AutoCAD conviértelas a malla "
            "(CONVTOMESH o MESHSMOOTH) antes de exportar."
        )
    if read.failed:
        warnings.append(
            f"{sum(read.failed.values())} entidades no se pudieron leer (ver entities.failed). "
            "Ejecuta AUDIT/RECOVER en el DWG."
        )
    for name, info in layers.items():
        if info.get("dropped_without_height"):
            warnings.append(
                f"Capa {name}: {info['dropped_without_height']} polígonos descartados por no "
                "tener altura. Añade default_height a su regla o revisa text_regex/attribute."
            )
        if info.get("ambiguous_heights"):
            warnings.append(
                f"Capa {name}: {info['ambiguous_heights']} polígonos con varios valores de "
                "altura dentro; se usó el más cercano al centro. Acota text_layers."
            )
        if info.get("nearby_text_heights"):
            warnings.append(
                f"Capa {name}: {info['nearby_text_heights']} alturas leídas de textos justo "
                "fuera del contorno (text_tolerance). Revísalas o pon text_tolerance: 0."
            )
        defaulted = info.get("heights", {}).get(HeightOrigin.DEFAULT.value, 0)
        partially_buried = (info.get("ground_check") or {}).get("partially_buried", 0)
        foundations = info.get("terrain_foundations", 0)
        if foundations and info.get("max_foundation_height_m", 0) > 1:
            warnings.append(
                f"Capa {name}: {foundations} edificios con zócalo automático adaptado al terreno "
                f"(máximo {info['max_foundation_height_m']:.2f} m). "
                "Es una aproximación geométrica: revisa las cotas de proyecto y las alturas "
                "asignadas, especialmente en laderas fuertes."
            )
        if partially_buried:
            warnings.append(
                f"Capa {name}: {partially_buried} sólidos parcialmente enterrados; el terreno "
                "supera la cubierta en parte de la huella. Revisa la altura asignada, la base "
                "y las curvas originales (ver detalle de apoyo). No se reasientan automáticamente."
            )
        if info.get("kind") == "building" and defaulted:
            warnings.append(
                f"Capa {name}: {defaulted} edificios usan default_height (no se encontró "
                "altura en el dibujo)."
            )
    if result.read.units_assumed:
        warnings.append("Unidades supuestas (metros): verifica la escala del modelo.")
    return warnings


def _fmt_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{k}×{v}" for k, v in sorted(counts.items())) or "—"


def _mb(size: int) -> str:
    return f"{size / 1_048_576:.2f} MB"


def render_markdown(report: dict[str, Any]) -> str:
    out = report["output"]
    lines = [
        f"# Informe de conversión: {report['source']}",
        "",
        f"- Generado por: {report['generator']}",
        f"- DXF: {report['dxf_version']} · unidades: {report['units']}"
        + (" (supuestas)" if report["units_assumed"] else ""),
        f"- Desplazamiento de origen (m, ejes DWG): {report['origin_offset']}",
        f"- Triángulos: {out['triangles']:,} · draw calls: {out['draw_calls']} · "
        f"tamaño: {_mb(out['total_size_bytes'])}"
        + (f" · compresión: {out['compression']}" if out["compression"] else ""),
        "",
        "## Avisos",
        "",
    ]
    lines += [f"- {w}" for w in report["warnings"]] or ["- Ninguno."]

    lines += ["", "## Entidades leídas por tipo", "", "| Tipo | Nº |", "|---|---:|"]
    lines += [f"| {t} | {n:,} |" for t, n in report["entities"]["read_by_type"].items()]

    lines += [
        "",
        "## Capas",
        "",
        "| Capa | Estado | Kind | Entidades | Objetos | Instancias | Triángulos |",
        "|---|---|---|---|---:|---|---:|",
    ]
    for name, info in report["layers"].items():
        instanced = _fmt_counts(info.get("instanced", {}))
        lines.append(
            f"| {name} | {info['status']} | {info.get('kind', '—')} | "
            f"{_fmt_counts(info['features'])} | {info.get('objects', 0):,} | {instanced} | "
            f"{info.get('triangles', 0):,} |"
        )

    converted = {n: i for n, i in report["layers"].items() if i["status"] == "converted"}
    lines += ["", "## Topología y alturas", ""]
    lines += [
        "| Capa | Cerrados por tolerancia | Lazos desde LINE/ARC | Colgantes | Duplicados | "
        "Reparados | Degenerados | Huecos | Alturas | Sin altura | Ambiguas |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|",
    ]
    for name, info in converted.items():
        t = info["topology"]
        lines.append(
            f"| {name} | {t['closed_by_tolerance']} | {t['loops_from_segments']} | "
            f"{t['dangling_segments']} | {t['duplicates_removed']} | {t['repaired']} | "
            f"{t['degenerate_dropped']} | {t['holes']} | {_fmt_counts(info['heights'])} | "
            f"{info['dropped_without_height']} | {info['ambiguous_heights']} |"
        )

    ground_layers = {n: i for n, i in converted.items() if i.get("ground_check")}
    if ground_layers:
        lines += [
            "",
            "## Apoyo sobre el terreno",
            "",
            "Tolerancia: 1 m. Cotas y coordenadas en metros, en los ejes originales del dibujo.",
            "Una intersección con ladera no demuestra un error del archivo. "
            "Con base_mode=terrain_min, "
            "el programa coloca una base horizontal en la cota mínima bajo la huella; "
            "la ladera puede superar la base e incluso la cubierta. Las alturas de origen "
            "default_height o block son valores de configuración, "
            "no alturas 3D verificadas del plano.",
            "Si la huella no está cubierta por el TIN, parte del apoyo se estima con el punto "
            "de terreno más cercano. Contrasta las cotas con las curvas originales "
            "antes de corregir.",
            "Con terrain_support=foundation, la planta horizontal se coloca en la cota máxima "
            "más el desplazamiento configurado; la altura asignada se mide desde esa planta. "
            "El zócalo baja hasta el terreno y la cubierta permanece horizontal. "
            "El zócalo es una aproximación automática, no una cimentación estructural calculada. "
            "Los casos foundation indican adaptaciones realizadas, no enterramientos pendientes.",
            "",
            "| Capa | Comprobados | Flotan | Enterrados | "
            "Intersecan ladera | Parcialmente enterrados |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for name, info in ground_layers.items():
            c = info["ground_check"]
            lines.append(
                f"| {name} | {c['checked']} | {c['floating']} | {c['buried']} | "
                f"{c.get('intersecting', 0)} | {c.get('partially_buried', 0)} |"
            )
        lines += [
            "",
            "### Objetos que revisar",
            "",
            "Los handles identifican entidades del DXF leído. En un DWG convertido, "
            "verifica su correspondencia en el DXF intermedio. "
            "El detalle completo está en report.json.",
            "",
            "| Capa | Caso | Handles | XY (m) | Z original | Base / cubierta | "
            "Terreno mín. / máx. | Altura / origen | Base aplicada | Dentro del TIN | "
            "Planta / zócalo |",
            "|---|---|---|---|---:|---|---|---|---|---|---|",
        ]

        def cell(value: object) -> str:
            return str(value).replace("|", "\\|").replace("\n", " ")

        def meters(value: float | None) -> str:
            return "—" if value is None else f"{value:.2f}"

        categories = {
            "floating": "Flota completamente",
            "buried": "Enterrado completamente",
            "intersecting": "Interseca la ladera",
            "partially_buried": "Enterrado parcialmente",
            "foundation": "Zócalo adaptado",
            "supported": "Apoyo sin incidencia de cota",
            "unverified_contact": "Contacto sin verificar",
        }
        for name, info in ground_layers.items():
            for d in info.get("ground_details", []):
                if d["category"] == "supported" and d["terrain_covered"] is True:
                    continue  # el JSON conserva la cobertura de todos los objetos comprobados
                xy = d["xy_m"]
                position = ", ".join(meters(v) for v in xy) if xy is not None else "—"
                identifiers = list(d["handles"])
                if d.get("insert_handle"):
                    identifiers.append(f"INSERT {d['insert_handle']} ({d.get('block', '')})")
                lines.append(
                    f"| {cell(name)} | {categories[d['category']]} | "
                    f"{cell(', '.join(identifiers))} | "
                    f"{position} | {meters(d['source_elevation_m'])} | "
                    f"{meters(d['base_m'])} / {meters(d['top_m'])} | "
                    f"{meters(d['terrain_min_m'])} / {meters(d['terrain_max_m'])} | "
                    f"{meters(d['height_m'])} / {d['height_origin']} | {d['base_mode']} | "
                    f"{ {True: 'sí', False: 'no', None: '—'}[d['terrain_covered']] } | "
                    f"{meters(d.get('floor_m'))} / {meters(d.get('foundation_height_m'))} |"
                )

    for title, key in (
        ("No soportadas", "unsupported"),
        ("Omitidas (sin geometría útil)", "skipped"),
        ("Fallidas", "failed"),
    ):
        rows = report["entities"][key]
        if rows:
            lines += ["", f"## {title}", "", "| Capa | Tipo | Nº |", "|---|---|---:|"]
            lines += [f"| {r['layer']} | {r['type']} | {r['count']} |" for r in rows]

    lines += [
        "",
        "## Archivos generados",
        "",
        "| Archivo | Tamaño | Triángulos |",
        "|---|---:|---:|",
    ]
    lines += [
        f"| {f['path']} | {_mb(f['size_bytes'])} | {f['triangles']:,} |" for f in out["files"]
    ]
    lines += ["", "## Tiempos (s)", "", "| Fase | s |", "|---|---:|"]
    lines += [f"| {k} | {v:.2f} |" for k, v in report["timings_s"].items()]
    return "\n".join(lines) + "\n"
