"""GLB comprimidos y proyectos teselados: se edita un maestro sin comprimir y lo entregado se
regenera desde él (teselas, manifest, exportación comprimida)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from ai_fixtures import write_model
from ai_helpers import FakeProvider, ask, op, plan, refs, service, targets

from extrude.ai_planner import load_project, model_objects
from extrude.master import MASTER_GLB, export_compressed, is_compressed
from extrude.materials_service import step
from extrude.node_tools import Compression, tools_dir, validate
from extrude.project_edit import ApplyRequest, apply_edit
from extrude.project_state import ProjectState

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not (tools_dir() / "node_modules" / "draco3dgltf").is_dir(),
    reason="Node o tools/node_modules no disponibles",
)


def edit(job: Path, responder: Any) -> dict[str, Any]:
    proposal = ask(
        service(FakeProvider(responder)),
        job,
        f"cambio {responder.__name__}",
        baseRevision=load_project(job / "out").state.current,
    )
    return apply_edit(
        job,
        ApplyRequest(
            planId=proposal["planId"],
            baseRevision=proposal["baseRevision"],
            operations=[o["id"] for o in proposal["operations"]],
        ),
        preview=False,
    )


def raise_e001(ctx: dict[str, Any]) -> str:
    return plan(
        op(
            "op1",
            "building.set_height",
            {"mode": "delta_m", "value": 3, "floorHeightM": None},
            targets(refs(ctx, "E-001")),
        )
    )


def by_label(data: bytes) -> dict[str, Any]:
    return {o.label: o for o in model_objects(data, "r")}


@pytest.mark.parametrize("method", [Compression.MESHOPT, Compression.DRACO])
def test_compressed_project_is_edited_on_its_master_and_exported_compressed(
    tmp_path: Path, method: Compression
) -> None:
    job = tmp_path / "job"
    write_model(job / "out", doubles=True, compression=method)
    out = job / "out"
    assert is_compressed(out / "model.glb") and not is_compressed(out / MASTER_GLB)
    original = (out / "model.glb").read_bytes()
    edit(job, raise_e001)
    assert (out / "model.glb").read_bytes() == original  # la entrega original no se toca
    current = by_label((out / "model.current.glb").read_bytes())
    assert current["E-001"].height == pytest.approx(15.0)
    assert current["DOBLE #3.2"].instance  # las instancias siguen siendo instancias
    exported = export_compressed(out, out / "model.current.glb", method.value, "rev-001")
    assert is_compressed(exported)
    # Reabrir el exportado comprimido: se decodifica y conserva la edición e identidades
    again = tmp_path / "again"
    (again / "out").mkdir(parents=True)
    shutil.copyfile(exported, again / "out" / "model.glb")
    reopened = load_project(again / "out")
    labels = {o.label: o for o in reopened.objects}
    assert labels["E-001"].height == pytest.approx(15.0)
    assert {o.id for o in reopened.objects} == {o.id for o in current.values()}
    (report,) = validate([exported])
    assert report["errors"] == 0


def test_uploaded_compressed_glb_without_project_gets_a_master(tmp_path: Path) -> None:
    source = write_model(tmp_path / "src", doubles=True)
    job = tmp_path / "job"
    (job / "out").mkdir(parents=True)
    from extrude.node_tools import compress

    compress(source / "model.glb", job / "out" / "model.glb", Compression.MESHOPT)
    project = load_project(job / "out")  # crea el maestro al abrir
    assert (job / "out" / MASTER_GLB).is_file()
    assert project.state.head.base == MASTER_GLB
    assert {o.label for o in project.objects} >= {"E-001", "DOBLE #1.1"}


def tiles_of(out: Path) -> dict[str, dict[str, Any]]:
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    found = {}
    for tile in manifest["tiles"]:
        for o in model_objects((out / tile["file"]).read_bytes(), "r"):
            found[o.id] = {"tile": tile["id"], "height": o.height, "x": o.x, "label": o.label}
    return found


def test_tiled_project_regenerates_tiles_and_manifest_from_the_master(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_model(job / "out", doubles=True, tiles=40.0)
    out = job / "out"
    assert not (out / "model.glb").exists() and (out / MASTER_GLB).is_file()
    before = tiles_of(out)
    e001 = next(i for i, t in before.items() if t["label"] == "E-001")
    assert len({t["tile"] for t in before.values()}) > 1
    edit(job, raise_e001)
    after = tiles_of(out)
    assert after[e001]["height"] == pytest.approx(15.0)
    assert after[e001]["tile"] == before[e001]["tile"]
    assert set(after) == set(before)  # nada duplicado ni perdido entre teselas
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["revision"] == "rev-001"

    def far_east(ctx: dict[str, Any]) -> str:
        return plan(op("op1", "object.move", {"dxM": 200, "dyM": 0}, targets(refs(ctx, "E-001"))))

    edit(job, far_east)  # cambia de tesela: aparece una nueva y la anterior lo pierde
    moved = tiles_of(out)
    assert moved[e001]["tile"] != before[e001]["tile"]
    assert set(moved) == set(before)
    step(out, "job", forward=False)  # deshacer regenera también las teselas
    assert tiles_of(out)[e001]["tile"] == before[e001]["tile"]
    for tile in json.loads((out / "manifest.json").read_text(encoding="utf-8"))["tiles"]:
        (report,) = validate([out / tile["file"]])
        assert report["errors"] == 0, (tile["id"], report["messages"][:3])


def test_failed_retile_leaves_the_published_project_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = tmp_path / "job"
    write_model(job / "out", tiles=40)
    out = job / "out"
    before = {p: p.read_bytes() for p in out.rglob("*.glb")}
    manifest = (out / "manifest.json").read_bytes()

    def fail(*args: Any, **kw: Any) -> None:
        raise OSError("retile failed")

    monkeypatch.setattr("extrude.project_state.regenerate_tiles", fail)
    with pytest.raises(OSError, match="retile failed"):
        edit(job, raise_e001)
    assert (out / "manifest.json").read_bytes() == manifest
    assert not (out / "model.current.glb").exists()
    assert all(p.read_bytes() == data for p, data in before.items())
    project = load_project(out)
    assert project.state.current == "rev-000"
    assert next(o.height for o in project.objects if o.label == "E-001") == 12


@pytest.mark.parametrize("write_before_failure", [False, True])
def test_failed_publication_rolls_back_files_and_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write_before_failure: bool
) -> None:
    job = tmp_path / "job"
    write_model(job / "out", tiles=40)
    out = job / "out"
    edit(job, raise_e001)
    before = {
        name: (out / name).read_bytes()
        for name in (
            "model.current.glb",
            "project.json",
            "manifest.json",
        )
    }
    old_tiles = set((out / "tiles").glob("*.glb"))

    original_save = ProjectState.save

    def fail(state: ProjectState) -> None:
        if write_before_failure:
            original_save(state)
        raise OSError("metadata write failed")

    monkeypatch.setattr(ProjectState, "save", fail)

    def further(ctx: dict[str, Any]) -> str:
        return raise_e001(ctx)

    with pytest.raises(OSError, match="metadata write failed"):
        edit(job, further)
    assert all((out / name).read_bytes() == data for name, data in before.items())
    assert set((out / "tiles").glob("*.glb")) == old_tiles
    assert load_project(out).state.current == "rev-001"


def test_old_manifests_keep_valid_immutable_tiles(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_model(job / "out", tiles=40)
    out = job / "out"
    manifests = []
    for _ in range(2):
        old = json.loads((out / "manifest.json").read_text())
        manifests.append({t["file"]: (out / t["file"]).read_bytes() for t in old["tiles"]})
        # Una descripción nueva evita reutilizar el plan preparado sobre una revisión anterior.
        edit(job, raise_e001)
    current = json.loads((out / "manifest.json").read_text())
    assert current["geometry_revision"] == load_project(out).geometry_revision
    for files in manifests:
        assert all((out / name).read_bytes() == data for name, data in files.items())
    step(out, "job", forward=False)
    assert load_project(out).state.current == "rev-001"
