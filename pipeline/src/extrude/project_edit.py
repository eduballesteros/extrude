"""Aplicar un plan de edición validado: transacción sobre el estado confirmado del proyecto.

- Usa el plan guardado por el servidor (`ai/plans/<planId>.json`), nunca uno reenviado.
- Rechaza planes obsoletos: si el proyecto cambió desde que se pidió, hay que pedirlo de nuevo.
- Ejecuta solo las operaciones marcadas, en orden de dependencias; una operación marcada exige
  las de las que depende. Las dudas se resuelven con objetos elegidos por el usuario y los datos
  que faltan (altura de planta) con valores suyos: nada de esto llama a la IA.
- Todo o nada: se edita una copia en memoria y, si una operación falla, no se escribe nada.
- Previsualizar escribe `model.preview.glb`; confirmar crea una revisión del proyecto (deshacer y
  rehacer la recorren). Las herramientas del visor (vistas, sol) se devuelven para ejecutarlas allí.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from extrude.ai_planner import (
    AiError,
    ObjectFacts,
    Project,
    current_presets,
    load_project,
    load_stored_plan,
    model_objects,
    target_height,
)
from extrude.arch_ops import (
    add_balconies,
    add_windows,
    hosted,
    hosted_conflicts,
    make_unique,
    place_site,
    raise_walls,
    read_building,
    set_ridge,
    setback,
)
from extrude.edit_tools import TOOLS, HeightArgs, MaterialArgs
from extrude.glb_build import remove_object
from extrude.glb_edit import (
    EditError,
    GlbEditor,
    current_height,
    duplicate,
    geometry_check,
    locate,
    object_center,
    set_height,
    transform,
    unit_key,
    unit_members,
)
from extrude.material_library import DEFAULT_LIBRARY, MaterialLibrary
from extrude.materiality import (
    AiTrace,
    Assignment,
    MaterialPlan,
    Origin,
    Parameters,
    validate_plan,
)
from extrude.materialize import GlbData, glb_catalog, materialize
from extrude.project_state import LOCK, write_atomic
from extrude.viewer_contract import ViewerContractError, validate_effects

PREVIEW_GLB = "model.preview.glb"


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    planId: str = Field(pattern=r"^plan-[0-9a-f]{16}$")  # noqa: N815
    baseRevision: str = Field(pattern=r"^rev-[0-9]{3,}$")  # noqa: N815
    operations: list[str] = Field(min_length=1, max_length=100)
    resolutions: dict[str, list[str]] = Field(default_factory=dict)
    inputs: dict[str, dict[str, float]] = Field(default_factory=dict)
    description: str | None = Field(default=None, max_length=10_000)
    # Efectos del visor ya preparados y validados en el cliente (vistas con su cámara final, sol
    # antes/después). Se guardan con la revisión: deshacer, rehacer y reabrir los restauran.
    viewer: dict[str, Any] | None = None


def parse_apply(body: bytes) -> ApplyRequest:
    try:
        return ApplyRequest.model_validate_json(body)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise AiError(
            400,
            "bad_request",
            f"Solicitud no válida: {first['loc']}: {first['msg']}",
            "Usa el visor de Extrude.",
        ) from None


def _next_material_revision(out: Path) -> str:
    numbers = [
        int(p.stem.split("-")[1])
        for p in (out / "materials").glob("mat-*.json")
        if p.stem.split("-")[1].isdigit()
    ]
    return f"mat-{max(numbers, default=0) + 1:03d}"


def _fail(
    op_id: str, message: str, results: list[dict[str, Any]], kind: str = "operation_failed"
) -> AiError:
    for result in results:
        if result["id"] == op_id:
            result.update(status="failed", message=message)
        elif result["status"] == "done":
            result.update(status="not_applied", message="No se aplicó: la transacción se canceló.")
    return AiError(
        422,
        kind,
        f"{op_id}: {message}",
        "No se cambió nada. Corrige o desmarca la operación.",
        results=results,
    )


def apply_edit(
    job_dir: Path,
    request: ApplyRequest,
    *,
    preview: bool,
    library: MaterialLibrary = DEFAULT_LIBRARY,
) -> dict[str, Any]:
    out = job_dir / "out"
    with LOCK:
        stored = load_stored_plan(job_dir, request.planId)
        project = load_project(out)
        current = project.state.current
        if stored["baseRevision"] != current or request.baseRevision != current:
            later = [
                r.summary
                for r in project.state.revisions
                if r.id > stored["baseRevision"] and r.id <= current
            ]
            raise AiError(
                409,
                "stale_plan",
                f"El plan se preparó sobre {stored['baseRevision']} y el proyecto está en "
                f"{current}." + (f" Cambios posteriores: {'; '.join(later[:5])}." if later else ""),
                "Vuelve a pedir la propuesta sobre el estado actual: no se aplica encima "
                "de cambios posteriores.",
                revision=current,
            )
        return _execute(job_dir, out, stored, project, request, preview, library)


def _execute(
    job_dir: Path,
    out: Path,
    stored: dict[str, Any],
    project: Project,
    request: ApplyRequest,
    preview: bool,
    library: MaterialLibrary,
) -> dict[str, Any]:
    ops = {op["id"]: op for op in stored["operations"]}
    order = [op["id"] for op in stored["operations"]]
    selected = set(request.operations)
    unknown = selected - set(ops)
    if unknown:
        raise AiError(
            400, "unknown_operation", f"Operaciones inexistentes: {sorted(unknown)}.", "Recarga."
        )
    by_id = {o.id: o for o in project.objects}
    results: list[dict[str, Any]] = [
        {
            "id": op_id,
            "tool": ops[op_id]["tool"],
            "status": "pending",
            "message": "",
            "objectIds": [],
        }
        for op_id in order
        if op_id in selected
    ]
    # 1) Objetivos finales: dependencias, estado, dudas resueltas por el usuario (respetando las
    #    exclusiones del encargo) y datos que faltaban. Nada se ejecuta todavía.
    plan_ops: list[tuple[dict[str, Any], list[str], dict[str, Any]]] = []
    for op_id in order:
        if op_id not in selected:
            continue
        op = ops[op_id]
        result = next(r for r in results if r["id"] == op_id)
        missing = [d for d in op["dependsOn"] if d not in selected]
        if missing:
            raise _fail(
                op_id,
                f"depende de {', '.join(missing)}, que no está marcada.",
                results,
                "dependency",
            )
        if op["status"] in ("blocked", "incompatible"):
            raise _fail(
                op_id, f"está {op['status']}: {' '.join(op['messages'])}", results, "not_executable"
            )
        spec = TOOLS[op["tool"]]
        args_model = spec.args.model_validate(op["args"])
        targets = list(op["objectIds"])
        if op["pending"]:
            chosen = request.resolutions.get(op["pending"])
            if not chosen:
                raise _fail(
                    op_id,
                    "su aclaración sigue pendiente: elige los objetos.",
                    results,
                    "needs_resolution",
                )
            unknown_ids = [i for i in chosen if i not in by_id]
            if unknown_ids:
                raise _fail(
                    op_id, f"objetos inexistentes en el proyecto: {unknown_ids[:3]}.", results
                )
            excluded = set(op.get("excludedIds", []))
            kept_out = [by_id[i].label for i in chosen if i in excluded]
            if kept_out:
                result["message"] = f"Se respeta la excepción del encargo: {', '.join(kept_out)}."
            targets = []
            for object_id in dict.fromkeys(chosen):
                if object_id in excluded:
                    continue
                reason = spec.check(by_id[object_id].info(), args_model)
                if reason:
                    raise _fail(
                        op_id, f"{by_id[object_id].label} no es compatible: {reason}.", results
                    )
                targets.append(object_id)
        args = dict(op["args"])
        # Datos que el usuario dio en el visor (altura de planta, cota de apoyo): sin IA
        given = request.inputs.get(op_id, {})
        allowed = {i["key"] for i in op.get("inputsNeeded", [])} | {"floorHeightM"}
        for key, value in given.items():
            if key in allowed:
                args[key] = value
        if op["tool"] == "site.place" and given.get("baseZM") is not None:
            args["support"] = "elevation"
        try:
            spec.args.model_validate(args)
        except ValueError as exc:
            raise _fail(
                op_id, f"datos no válidos: {str(exc)[:200]}", results, "needs_input"
            ) from None
        if op["tool"] == "building.set_height":
            HeightArgs.model_validate(args)
            lacking = [by_id[i].label for i in targets if target_height(by_id[i], args) is None]
            if lacking:
                raise _fail(
                    op_id,
                    f"falta la altura de planta ({', '.join(lacking[:3])}).",
                    results,
                    "needs_input",
                )
        if spec.targets == "required" and not targets:
            raise _fail(op_id, "no tiene objetivos (tras aplicar las excepciones).", results)
        missing_input = [
            i["label"] for i in op.get("inputsNeeded", []) if args.get(i["key"]) is None
        ]
        if missing_input:
            raise _fail(
                op_id, f"falta un dato: {', '.join(missing_input)}.", results, "needs_input"
            )
        plan_ops.append((op, targets, args))

    # 2) Revalidar el conjunto YA resuelto: lo que se revisó debe ser lo que se ejecuta
    _revalidate(plan_ops, project, stored, results, by_id)

    # 3) Ejecutar sobre una copia en memoria, en orden de dependencias
    editor = GlbEditor(project.data)
    geometry_changed = False
    material_ops: list[tuple[dict[str, Any], list[str], dict[str, Any]]] = []
    copies: dict[str, list[str]] = {}
    viewer_ops = []
    warnings: list[str] = []
    for op, targets, args in plan_ops:
        result = next(r for r in results if r["id"] == op["id"])
        tool = op["tool"]
        try:
            if TOOLS[tool].executor == "viewer":
                viewer_ops.append(
                    {"id": op["id"], "tool": tool, "args": args, "objectIds": targets}
                )
                result.update(
                    status="viewer",
                    objectIds=targets,
                    message="Preparado en el visor; se guarda con la revisión.",
                )
                continue
            if tool == "material.assign":
                missing_targets = set(targets) - set(locate(editor.doc))
                if missing_targets:
                    raise EditError(
                        "missing",
                        "El objeto al que se asigna material ya se eliminó.",
                        "Revisa el orden de las operaciones.",
                    )
                material_ops.append((op, targets, args))
                result.update(status="done", objectIds=targets)
                continue
            located = locate(editor.doc)
            details: list[dict[str, Any]] = []
            affected: list[str] = []
            salt = f"{request.planId}:{op['id']}"
            if tool in NEW_TOOLS:
                made, details, auto = _run_new_tool(
                    editor, tool, args, targets, located, by_id, project, salt
                )
                for host, new_ids in made.items():
                    copies.setdefault(host, []).extend(new_ids)
                for ids, surface, material in auto:
                    material_ops.append(
                        (
                            {"id": op["id"], "reason": "Material del elemento añadido."},
                            ids,
                            {
                                "surface": surface,
                                "materialId": material,
                                "color": None,
                                "roughness": None,
                                "pieceSizeM": None,
                                "rotationDeg": None,
                            },
                        )
                    )
                geometry_check(editor)
                geometry_changed = True
                fresh = [i for v in made.values() for i in v if i not in by_id]
                result.update(
                    status="done", objectIds=list(dict.fromkeys(targets + fresh)), details=details
                )
                continue
            # Unidades físicas: una copia de bloque se edita UNA vez aunque se citen varias piezas
            units: dict[tuple[int, int | str | None], str] = {}
            for object_id in targets:
                where = located.get(object_id)
                if where is None:
                    raise EditError("missing", f"{object_id} no está en el modelo.", "Recarga.")
                if tool == "building.set_height":
                    units[(where.node, object_id)] = object_id
                else:
                    units.setdefault(unit_key(where), object_id)
            for object_id in units.values():
                where = located[object_id]
                if tool == "building.set_height":
                    height = target_height(by_id[object_id], args, current_height(editor, where))
                    if height is None:
                        raise EditError("input", "Falta la altura de planta.", "Indícala.")
                    floor_z = read_building(editor, where).floor_z
                    clash = hosted_conflicts(editor, object_id, floor_z + height)
                    if clash:
                        raise EditError(
                            "hosted",
                            f"{by_id[object_id].label}: quedarían por encima de la nueva altura "
                            + ", ".join(clash[:4])
                            + ".",
                            "Elimina antes esos elementos o pide una altura mayor.",
                        )
                    details.append(
                        {"label": by_id[object_id].label, **set_height(editor, where, height)}
                    )
                    affected.append(object_id)
                    continue
                members = unit_members(editor.doc, where)
                kept_out = [m for m in members if m in set(op.get("excludedIds", []))]
                if kept_out:
                    raise EditError(
                        "excluded_piece",
                        f"La copia de {by_id[object_id].label} incluye una pieza excluida "
                        f"({', '.join(by_id[m].label for m in kept_out if m in by_id)}): moverla, "
                        "girarla o duplicarla la arrastraría.",
                        "Independiza antes esa copia (block.make_unique) o quita la excepción.",
                    )
                affected += members
                if tool in ("object.move", "object.rotate"):
                    pivot = object_center(editor, where)
                    dx = args.get("dxM", 0.0)
                    dy = args.get("dyM", 0.0)
                    angle = args.get("angleDeg", 0.0)
                    transform(editor, where, dx=dx, dy=dy, angle_deg=angle)
                    # Ventanas y balcones van con su edificio (mismo giro y centro)
                    for element, _ in hosted(editor, object_id):
                        transform(editor, element, dx=dx, dy=dy, angle_deg=angle, pivot=pivot)
                        affected.append(element.object_id)
                elif tool == "object.duplicate":
                    created: list[str] = []
                    for k in range(1, int(args["copies"]) + 1):
                        pairs = duplicate(
                            editor,
                            where,
                            args["dxM"] * k,
                            args["dyM"] * k,
                            f"{request.planId}:{op['id']}:{k}",
                        )
                        # La copia incluye sus elementos de fachada y conserva la relación host.
                        host_pairs = list(pairs)
                        for old_host, new_host in host_pairs:
                            for element, row in hosted(editor, old_host):
                                children = duplicate(
                                    editor,
                                    element,
                                    args["dxM"] * k,
                                    args["dyM"] * k,
                                    f"{request.planId}:{op['id']}:{k}",
                                )
                                for _, child_id in children:
                                    current_locations = locate(editor.doc)
                                    child = current_locations[child_id]
                                    new_where = current_locations[new_host]
                                    host_table = editor.doc["nodes"][new_where.node]["extras"][
                                        "objects"
                                    ]
                                    child_table = editor.doc["nodes"][child.node]["extras"][
                                        "objects"
                                    ]
                                    child_table["host"][child.index] = new_host
                                    child_table["label"][child.index] = (
                                        f"{host_table['label'][new_where.index]} · {row['label']}"
                                    )
                                pairs.extend(children)
                        for old, new in pairs:  # correspondencia por pieza
                            copies.setdefault(old, []).append(new)
                            created.append(new)
                        located = locate(editor.doc)
                        where = located[object_id]
                    details.append({"label": by_id[object_id].label, "created": created})
            geometry_check(editor)
            geometry_changed = True
            result.update(status="done", objectIds=list(dict.fromkeys(affected)), details=details)
        except EditError as exc:
            raise _fail(op["id"], f"{exc.problem} {exc.fix}", results) from None

    data = editor.encode() if geometry_changed else project.data
    revision = project.geometry_revision
    if geometry_changed:
        digest = hashlib.sha256(
            json.dumps(
                {
                    "from": project.geometry_revision,
                    "plan": request.planId,
                    "ops": [
                        (op["id"], op["tool"], args, targets) for op, targets, args in plan_ops
                    ],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        revision = f"sha256:{digest}"
        editor = GlbEditor(data)
        extras = editor.doc["scenes"][0].setdefault("extras", {})
        extras["source_geometry_revision"] = project.source_revision
        extras["geometry_revision"] = revision
        extras["edited_by"] = request.planId
        data = editor.encode()
        new_objects = model_objects(data, revision)
        warnings += _overlaps(
            new_objects,
            {i for _, t, _ in plan_ops for i in t} | {i for v in copies.values() for i in v},
        )

    catalog = glb_catalog(GlbData(data), revision)
    plan = _material_plan(
        project,
        stored,
        request,
        material_ops,
        copies,
        revision,
        out,
        preview,
        library,
        {o["id"] for o in catalog["objects"]},
    )
    materialized = None
    if plan is not None:
        issues = validate_plan(plan, catalog, library)
        if issues:
            raise AiError(
                422,
                "plan_invalid",
                "El plan de materiales resultante no es válido.",
                "; ".join(i.message for i in issues[:3]),
                results=results,
            )
        materialized, _ = materialize(data, plan, library)
    model = materialized or data
    if preview:
        write_atomic(out / PREVIEW_GLB, model)
        return {
            "preview": True,
            "url": f"/api/jobs/{job_dir.name}/{PREVIEW_GLB}",
            "results": results,
            "viewerOps": viewer_ops,
            "warnings": warnings,
            "geometryChanged": geometry_changed,
        }
    if viewer_ops and request.viewer is None:
        raise AiError(
            422,
            "viewer_unprepared",
            "Las vistas o el sol no se prepararon en el visor antes de confirmar.",
            "Vuelve a confirmar desde el visor de Extrude.",
            results=results,
        )
    viewer_state = None
    if viewer_ops:
        try:
            viewer_state = validate_effects(request.viewer, viewer_ops)
        except ViewerContractError as exc:
            raise AiError(
                422,
                "viewer_invalid",
                str(exc),
                "Vuelve a confirmar desde el visor de Extrude.",
                results=results,
            ) from None
    kind = "edit" if geometry_changed else "materials" if material_ops else "viewer"
    done = [r for r in results if r["status"] in ("done", "viewer")]
    revision_entry = project.state.commit(
        kind=kind,
        summary=str(stored.get("summary", ""))[:300],
        base_bytes=data if geometry_changed else None,
        geometry_revision=revision,
        plan_text=plan.to_json() if plan is not None else None,
        material_revision=plan.material_revision if plan is not None else None,
        materialized=materialized,
        plan_id=request.planId,
        viewer=viewer_state,
        operations=[
            {
                "id": r["id"],
                "tool": r["tool"],
                "status": r["status"],
                "objects": len(r["objectIds"]),
            }
            for r in done
        ],
    )
    return {
        "preview": False,
        "revision": revision_entry.id,
        "results": results,
        "viewerOps": viewer_ops,
        "warnings": warnings,
        "geometryChanged": geometry_changed,
        "project": project.state.public(job_dir.name),
        "plan": json.loads(plan.to_json()) if plan is not None else None,
    }


NEW_TOOLS = {
    "site.place",
    "object.delete",
    "facade.add_windows",
    "facade.add_balconies",
    "building.setback",
    "building.raise_walls",
    "roof.set_ridge",
    "block.make_unique",
}


def _run_new_tool(
    editor: GlbEditor,
    tool: str,
    args: dict[str, Any],
    targets: list[str],
    located: dict[str, Any],
    by_id: dict[str, ObjectFacts],
    project: Project,
    salt: str,
) -> tuple[dict[str, list[str]], list[dict[str, Any]], list[tuple[list[str], str, str]]]:
    """Ejecuta una herramienta de las ampliaciones. Devuelve: objetos nuevos por origen (para
    heredar materiales), detalles para el informe y materiales automáticos (ids, superficie,
    material) que entran en el plan como asignaciones revisadas."""
    created: dict[str, list[str]] = {}
    details: list[dict[str, Any]] = []
    auto: list[tuple[list[str], str, str]] = []
    if tool == "site.place":
        objects = project.objects
        center = (
            (
                (min(o.x for o in objects) + max(o.x for o in objects)) / 2,
                (min(o.y for o in objects) + max(o.y for o in objects)) / 2,
            )
            if objects
            else (0.0, 0.0)
        )
        origin = editor.doc["scenes"][0].get("extras", {}).get("origin_offset") or [0, 0, 0]
        ids = place_site(
            editor, args, [located[t] for t in targets], center, float(origin[2]), salt
        )
        created["site"] = ids
        details.append({"asset": args["assetId"], "count": len(ids)})
        return created, details, auto
    done_units: set[Any] = set()
    for object_id in targets:
        where = locate(editor.doc).get(object_id)
        if where is None:
            raise EditError("missing", f"{object_id} no está en el modelo.", "Recarga.")
        label = by_id[object_id].label if object_id in by_id else object_id
        floor_height = by_id[object_id].floor_height if object_id in by_id else None
        if tool == "object.delete":
            key = unit_key(where)
            if key not in done_units:
                remove_object(editor, where)
                done_units.add(key)
            details.append({"label": label, "deleted": True})
        elif tool == "facade.add_windows":
            ids = add_windows(editor, where, args["orientation"], args, floor_height, salt, label)
            created.setdefault("facade", []).extend(ids)
            glass = [i for i in ids if _layer_of(editor, i) == "VENTANAS"]
            frame = [i for i in ids if i not in glass]
            auto.append((glass, "generic", args.get("glassMaterialId") or "glass_blue"))
            auto.append((frame, "generic", args.get("frameMaterialId") or "metal_dark"))
            details.append({"label": label, "objects": len(ids)})
        elif tool == "facade.add_balconies":
            ids = add_balconies(editor, where, args["orientation"], args, floor_height, salt, label)
            created.setdefault("facade", []).extend(ids)
            auto.append((ids, "generic", args.get("materialId") or "concrete_exposed"))
            details.append({"label": label, "objects": len(ids)})
        elif tool == "building.setback":
            ids, info = setback(editor, where, args, label, salt, set_height)
            created[object_id] = ids  # el ático hereda los materiales del edificio
            details.append({"label": label, **info})
        elif tool == "building.raise_walls":
            details.append({"label": label, **raise_walls(editor, where, args["deltaM"])})
        elif tool == "roof.set_ridge":
            details.append({"label": label, **set_ridge(editor, where, args["ridgeHeightM"])})
        elif tool == "block.make_unique":
            key = unit_key(where)
            if key in done_units:
                continue
            done_units.add(key)
            ids = make_unique(editor, where, {o.id: o.label for o in by_id.values()})
            details.append({"label": label, "unique": ids})
    return created, details, auto


def _layer_of(editor: GlbEditor, object_id: str) -> str:
    where = locate(editor.doc).get(object_id)
    return where.layer if where else ""


def _revalidate(
    plan_ops: list[tuple[dict[str, Any], list[str], dict[str, Any]]],
    project: Project,
    stored: dict[str, Any],
    results: list[dict[str, Any]],
    by_id: dict[str, ObjectFacts],
) -> None:
    """Comprueba el conjunto de operaciones con sus objetivos FINALES (tras resolver dudas):
    dos materiales distintos en el mismo objeto y superficie, conflictos declarados sobre esos
    objetos y asignaciones manuales protegidas. Un conflicto no se resuelve por orden."""
    owners: dict[tuple[str, str], tuple[str, str]] = {}
    declared = {
        i for c in stored.get("conflicts", []) if not c.get("operations") for i in c["objectIds"]
    }
    manual: dict[tuple[str, str], str] = {}
    if project.plan is not None:
        for item in project.plan.assignments:
            if item.origin is Origin.MANUAL:
                for object_id in item.object_ids:
                    manual[(object_id, item.surface)] = item.material_id
    for op, targets, args in plan_ops:
        hit = sorted({by_id[i].label for i in targets if i in declared})
        if hit:
            raise _fail(
                op["id"],
                f"afecta a objetos con un conflicto declarado: {', '.join(hit)}.",
                results,
                "conflict",
            )
        if op["tool"] != "material.assign":
            continue
        kept = []
        for object_id in targets:
            key = (object_id, args["surface"])
            if key in manual and manual[key] != args["materialId"]:
                kept.append(by_id[object_id].label)
                continue
            other = owners.get(key)
            if other and other[1] != args["materialId"]:
                raise _fail(
                    op["id"],
                    f"{by_id[object_id].label} recibiría {other[1]} ({other[0]}) y "
                    f"{args['materialId']} en la misma superficie: elige uno.",
                    results,
                    "conflict",
                )
            owners[key] = (op["id"], args["materialId"])
        if kept:
            result = next(r for r in results if r["id"] == op["id"])
            result["message"] = (
                result["message"] + " " if result["message"] else ""
            ) + f"Se conserva la asignación manual de {', '.join(kept[:8])}."


def _material_plan(
    project: Project,
    stored: dict[str, Any],
    request: ApplyRequest,
    material_ops: list[tuple[dict[str, Any], list[str], dict[str, Any]]],
    copies: dict[str, list[str]],
    revision: str,
    out: Path,
    preview: bool,
    library: MaterialLibrary,
    live_ids: set[str],
) -> MaterialPlan | None:
    """Plan confirmado + copias que heredan materiales + asignaciones nuevas (sin pisar las
    manuales protegidas)."""
    base = project.plan
    if base is None and not material_ops:
        return None
    assignments: list[Assignment] = list(base.assignments) if base else []
    manual = {
        (i, a.surface): a.material_id
        for a in assignments
        if a.origin is Origin.MANUAL
        for i in a.object_ids
    }
    for op, targets, args in material_ops:
        parsed = MaterialArgs.model_validate(args)
        ids = [i for i in targets if (i, parsed.surface) not in manual]
        if not ids:
            continue
        preset = current_presets(library)[parsed.materialId]
        params: dict[str, Any] = {}
        if parsed.color:
            params["color"] = parsed.color.lower()
        if parsed.roughness is not None:
            params["roughness"] = parsed.roughness
        if parsed.rotationDeg is not None:
            params["rotation_deg"] = parsed.rotationDeg
        if parsed.pieceSizeM:
            tile = preset.tile_for_piece((parsed.pieceSizeM[0], parsed.pieceSizeM[1]))
            params["tile_size_m"] = (min(tile[0], 100.0), min(tile[1], 100.0))
        taken = set(ids)
        assignments = [
            a.model_copy(update={"object_ids": [i for i in a.object_ids if i not in taken]})
            if a.surface == parsed.surface
            else a
            for a in assignments
        ]
        assignments = [a for a in assignments if a.object_ids]
        assignments.append(
            Assignment(
                object_ids=ids,
                surface=parsed.surface,
                material_id=preset.id,
                material_version=preset.version,
                parameters=Parameters(**params),
                origin=Origin.AI_REVIEWED,
                reason=str(op.get("reason", ""))[:500] or None,
            )
        )
    if copies:  # las copias heredan los materiales finales del original (también los nuevos)
        assignments = [
            a.model_copy(
                update={
                    "object_ids": a.object_ids
                    + [n for i in a.object_ids for n in copies.get(i, [])]
                }
            )
            for a in assignments
        ]
    assignments = [
        a.model_copy(update={"object_ids": [i for i in a.object_ids if i in live_ids]})
        for a in assignments
        if any(i in live_ids for i in a.object_ids)
    ]
    material_revision = (
        (base.material_revision if base else "mat-000") if preview else _next_material_revision(out)
    )
    trace = (
        AiTrace(
            provider=stored["provider"],
            model=str(stored["model"])[:100],
            simulated=bool(stored.get("simulated")),
            prompt_version=stored["promptVersion"],
            summary=str(stored.get("summary", ""))[:800],
            explicit_requirements=[str(s)[:400] for s in stored.get("explicitRequirements", [])][
                :40
            ],
            assumptions=[str(s)[:400] for s in stored.get("assumptions", [])][:40],
            created_at=stored.get("createdAt"),
        )
        if material_ops
        else (base.ai if base else None)
    )
    return MaterialPlan(
        schema_version=1,
        project_id=base.project_id if base else "visor",
        geometry_revision=revision,
        material_revision=material_revision,
        catalog_version=library.version,
        assignments=assignments,
        omissions=base.omissions if base else [],
        description=(
            request.description
            if material_ops and request.description
            else base.description
            if base
            else None
        ),
        ai=trace,
    )


def _overlaps(objects: list[ObjectFacts], changed: set[str]) -> list[str]:
    """Aviso (no error) si un edificio movido, girado o copiado se solapa en planta con otro."""
    boxes = {
        o.id: (o.x - o.width / 2, o.y - o.depth / 2, o.x + o.width / 2, o.y + o.depth / 2, o.label)
        for o in objects
        if o.kind == "building"
    }
    warnings = []
    for object_id in changed:
        if object_id not in boxes:
            continue
        a = boxes[object_id]
        for other, b in boxes.items():
            if other == object_id or (other in changed and other < object_id):
                continue
            if a[4].startswith(f"{b[4]}.") or b[4].startswith(f"{a[4]}."):
                continue  # un ático o una pieza sobre su propio edificio no es un solape
            if (
                a[0] < b[2] - 0.01
                and b[0] < a[2] - 0.01
                and a[1] < b[3] - 0.01
                and b[1] < a[3] - 0.01
            ):
                warnings.append(f"{a[4]} se solapa en planta con {b[4]}.")
    return warnings
