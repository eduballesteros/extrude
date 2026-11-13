"""Reparación de proyectos existentes desde la aplicación: revisión del apoyo, capas
pendientes, reconversión en el mismo proyecto, recuperación del resultado anterior, reapertura
e identidad de la ejecución. Fixtures sintéticas; un «proyecto antiguo» es uno convertido con
la configuración que generaban versiones anteriores y sin los archivos que ahora se guardan."""

from __future__ import annotations

import base64
import json
import struct
import threading
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import ezdxf
import pytest
import yaml
from test_server_async import CONFIG_HEADER, call, poll

from extrude import build_info
from extrude.config import parse_config
from extrude.layer_heights import BLOCK_START, apply_adjustments, current_adjustments, describe
from extrude.server import create_server

# Lo que escribía el borrador antiguo: Z como altura y base fija en 0, sin apoyo en el terreno
OLD_YAML = """\
# Borrador generado por `extrude init-config` para plano.dxf
units: auto
recenter: true
tolerance: 0.01

layers:
  # Capa BLOQUES: LWPOLYLINE×3
  - match: BLOQUES        # comentario del usuario que debe conservarse
    kind: building
    height_from: elevation
    floor_height: 3.0
    default_height: 9.0
  - match: CURVAS
    kind: terrain
  - match: CASAS
    kind: building
    height_from: text
    text_regex: '(\\d+)\\s*P'
    floor_height: 3.0
    default_height: 9.0
    base_from_elevation: true
  - match: "*"
    kind: ignore
"""


def old_drawing(path: Path) -> Path:
    doc = ezdxf.new("R2018")
    doc.units = 6
    msp = doc.modelspace()
    for x in range(0, 101, 10):  # ladera entre -60 y -40
        msp.add_lwpolyline(
            [(x, 0), (x, 100)], dxfattribs={"layer": "CURVAS", "elevation": -60 + x / 5}
        )
    for x, z in ((10, -58.0), (40, -52.0), (70, 0.0)):  # cotas negativas o cero
        msp.add_lwpolyline(
            [(x, 10), (x + 10, 10), (x + 10, 20), (x, 20)],
            close=True,
            dxfattribs={"layer": "BLOQUES", "elevation": z},
        )
    # Edificio con altura fiable (rótulo) y base explícita
    msp.add_lwpolyline(
        [(10, 60), (20, 60), (20, 70), (10, 70)],
        close=True,
        dxfattribs={"layer": "CASAS", "elevation": -57.0},
    )
    msp.add_text("5P", dxfattribs={"layer": "ROTULOS", "insert": (14, 64)})
    for x in (40, 60):  # capa de nombre desconocido con contornos cerrados
        msp.add_lwpolyline(
            [(x, 60), (x + 8, 60), (x + 8, 68), (x, 68)],
            close=True,
            dxfattribs={"layer": "SECTOR-B"},
        )
    doc.saveas(path)
    return path


@pytest.fixture
def server(tmp_path: Path) -> Iterator[str]:
    srv = create_server("127.0.0.1", 0, jobs_dir=tmp_path / "jobs", max_mb=5)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def post_json(url: str, body: Any) -> tuple[int, Any]:
    result: tuple[int, Any] = call(
        url, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    return result


def old_project(base: str, tmp_path: Path, *, legacy_files: bool = True) -> str:
    """Convierte con el YAML antiguo y deja el trabajo como lo dejaban versiones anteriores."""
    path = old_drawing(tmp_path / "plano.dxf")
    headers = {CONFIG_HEADER: base64.b64encode(OLD_YAML.encode()).decode()}
    code, accepted = call(f"{base}/api/convert?name=plano.dxf&async=1", path.read_bytes(), headers)
    assert code == 202, accepted
    assert poll(base, accepted["status"])[0]["state"] == "done"
    job: str = accepted["job"]
    if legacy_files:
        job_dir = tmp_path / "jobs" / job
        (job_dir / "job.json").unlink()
        (job_dir / "result.json").unlink()
        report_path = job_dir / "out" / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        for key in ("summary", "pending_layers", "terrain"):  # el informe antiguo no los tenía
            del report[key]
        report_path.write_text(json.dumps(report), encoding="utf-8")
    return job


def model(base: str, job: str) -> dict[str, Any]:
    with urllib.request.urlopen(f"{base}/api/jobs/{job}/model.glb") as resp:  # noqa: S310
        assert resp.headers["Cache-Control"] == "no-store"
        data = resp.read()
    length = struct.unpack("<I", data[12:16])[0]
    parsed: dict[str, Any] = json.loads(data[20 : 20 + length])
    return parsed


def layer_extras(gltf: dict[str, Any], layer: str) -> dict[str, Any]:
    return next(n["extras"] for n in gltf["nodes"] if (n.get("extras") or {}).get("layer") == layer)


def revision(gltf: dict[str, Any]) -> str:
    return str(gltf["scenes"][0]["extras"]["geometry_revision"])


def reconvert(base: str, job: str, body: dict[str, Any]) -> dict[str, Any]:
    code, accepted = post_json(f"{base}/api/jobs/{job}/reconvert", body)
    assert code == 202, accepted
    final, _ = poll(base, accepted["status"])
    assert final["state"] == "done", final
    return dict(final["result"])


# --- Ajustes del YAML ---------------------------------------------------------------------------


def test_support_repair_keeps_rules_comments_and_reliable_heights() -> None:
    edited = apply_adjustments(OLD_YAML, {"BLOQUES": {"support": "auto"}})
    assert BLOCK_START in edited
    assert "# comentario del usuario que debe conservarse" in edited
    original_rules = [line for line in OLD_YAML.splitlines() if line.strip()]
    assert all(line in edited for line in original_rules)  # nada de lo escrito se pierde
    config = parse_config(yaml.safe_load(edited))
    rule = config.rule_for("BLOQUES")
    assert rule is not None and rule.base_auto and rule.match == "BLOQUES"
    assert rule.height_from is not None and rule.default_height == 9.0  # fuentes conservadas
    other = config.rule_for("CASAS")
    assert other is not None and other.base_from_elevation and not other.base_auto  # ajena
    assert current_adjustments(edited) == {"BLOQUES": {"support": "auto"}}


def test_height_support_and_kind_adjustments_combine_and_revert() -> None:
    text = apply_adjustments(OLD_YAML, {"BLOQUES": {"support": "auto"}})
    text = apply_adjustments(text, {"BLOQUES": {"height": 15}})  # cambiar altura conserva apoyo
    rule = parse_config(yaml.safe_load(text)).rule_for("BLOQUES")
    assert rule is not None and rule.base_auto and rule.height == 15 and rule.height_from is None
    assert current_adjustments(text) == {"BLOQUES": {"support": "auto", "height": 15.0}}

    text = apply_adjustments(text, {"SECTOR-B": {"kind": "building"}, "NOTAS": {"kind": "ignore"}})
    config = parse_config(yaml.safe_load(text))
    new = config.rule_for("SECTOR-B")
    assert new is not None and new.kind.value == "building" and new.base_auto
    assert new.default_height == 9.0  # provisional, como en una importación nueva
    reviewed = config.rule_for("NOTAS")
    assert reviewed is not None and reviewed.kind.value == "ignore" and reviewed.reviewed
    by_name = {entry.name: entry for entry in describe(text, ["BLOQUES", "SECTOR-B", "CASAS"])}
    assert by_name["BLOQUES"].support_adjusted and by_name["BLOQUES"].base_mode == "auto"
    assert by_name["SECTOR-B"].kind_adjusted and by_name["CASAS"].base_mode == "elevation"

    text = apply_adjustments(text, {"BLOQUES": {"height": None}})
    assert current_adjustments(text)["BLOQUES"] == {"support": "auto"}
    back = apply_adjustments(
        text,
        {"BLOQUES": {"support": None}, "SECTOR-B": {"kind": None}, "NOTAS": {"kind": None}},
    )
    assert back == OLD_YAML


def test_blocks_written_by_the_previous_version_are_still_read() -> None:
    legacy = OLD_YAML.replace(
        "layers:\n",
        "layers:\n"
        "  # >>> extrude-visor: alturas fijadas en el panel de capas (bloque gestionado)\n"
        "  # capa: BLOQUES\n"
        "  - match: BLOQUES\n    kind: building\n    height: 21.5\n"
        "  # <<< extrude-visor\n",
    )
    assert current_adjustments(legacy) == {"BLOQUES": {"height": 21.5}}
    updated = apply_adjustments(legacy, {"BLOQUES": {"support": "auto"}})
    assert current_adjustments(updated) == {"BLOQUES": {"height": 21.5, "support": "auto"}}
    assert updated.count("extrude-visor") == 2


# --- Proyecto antiguo: revisar, reparar, recuperar, reabrir -------------------------------------


def test_old_project_is_listed_reviewed_and_repaired_in_place(server: str, tmp_path: Path) -> None:
    job = old_project(server, tmp_path)
    before = model(server, job)
    blocks = layer_extras(before, "BLOQUES")
    # El fallo de origen: cotas ≤ 0 tomadas como altura → 9 m provisionales desde la cota 0
    assert blocks["objects"]["base"] == [0.0, 0.0, 0.0]
    assert blocks["objects"]["height_origin"] == ["default_height"] * 3
    assert blocks["ground_check"]["floating"] == 3

    code, listing = call(f"{server}/api/projects")
    assert code == 200
    (entry,) = listing["projects"]
    assert entry["job"] == job and entry["floating"] == 3 and entry["outdated"] is True

    # Se puede reabrir aunque no guardara su resultado
    code, result = call(f"{server}/api/jobs/{job}/result")
    assert code == 200 and result["revision"] == revision(before)

    code, review = call(f"{server}/api/jobs/{job}/review")
    assert code == 200 and review["repeatable"] and review["outdated"]
    assert review["pending"] is None  # el informe antiguo no analizaba las capas ignoradas
    assert review["terrain"]["status"] == "valid" and review["canRestore"] is False
    by_layer = {s["layer"]: s for s in review["support"]}
    assert by_layer["BLOQUES"]["recommended"] and by_layer["BLOQUES"]["floating"] == 3
    assert by_layer["BLOQUES"]["baseMode"] == "fixed"
    # Base explícita: se puede cambiar, pero no se propone por defecto
    assert by_layer["CASAS"]["baseMode"] == "elevation"
    assert by_layer["CASAS"]["canRepair"] and not by_layer["CASAS"]["recommended"]

    result = reconvert(server, job, {"adjust": {"BLOQUES": {"support": "auto"}}})
    assert result["job"] == job
    after = model(server, job)
    assert revision(after) != revision(before)
    # GLB servido, informe y resultado guardado pertenecen a la misma revisión
    assert result["revision"] == revision(after) == result["report"]["geometry_revision"]
    assert result["build"] == build_info.LOADED[:12]
    blocks = layer_extras(after, "BLOQUES")
    assert blocks["ground_check"]["floating"] == 0 and blocks["ground_check"]["partial_gap"] == 0
    assert blocks["objects"]["base_origin"] == ["terrain"] * 3
    assert sorted(blocks["objects"]["base"]) == pytest.approx([-56.0, -50.0, -44.0])
    # Lo ajeno a la reparación no cambia: altura fiable del rótulo y base explícita
    houses = layer_extras(after, "CASAS")
    assert houses["objects"]["height"] == [15.0]
    assert houses["objects"]["height_origin"] == ["text"]
    assert houses["objects"]["base"] == [-57.0]

    code, review = call(f"{server}/api/jobs/{job}/review")
    assert review["outdated"] is False and review["canRestore"] is True
    # Borrador generado: la reparación aplica la política de una importación nueva, y la Z
    # deja de tomarse como altura (antes era una suposición del borrador)
    assert review["adjustments"] == {"BLOQUES": {"support": "auto", "z_height": "ignore"}}
    assert blocks["objects"]["height"] == [9.0, 9.0, 9.0]
    assert blocks["objects"]["height_confidence"] == ["provisional"] * 3
    assert [p["name"] for p in review["pending"]] == ["SECTOR-B"]
    assert review["summary"]["provisional_heights"] == 3  # siguen siendo provisionales
    by_layer = {s["layer"]: s for s in review["support"]}
    assert by_layer["BLOQUES"]["baseMode"] == "auto" and not by_layer["BLOQUES"]["recommended"]

    # Reabrir no recupera en silencio la geometría antigua
    code, reopened = call(f"{server}/api/jobs/{job}/result")
    assert reopened["revision"] == revision(after)
    code, status = call(f"{server}/api/jobs/{job}/project")
    assert code == 200 and status["geometryRevision"] == revision(after)


def test_pending_layer_is_classified_from_the_application(server: str, tmp_path: Path) -> None:
    job = old_project(server, tmp_path, legacy_files=False)
    _, review = call(f"{server}/api/jobs/{job}/review")
    (pending,) = review["pending"]
    assert pending["name"] == "SECTOR-B" and pending["evidence"]["closed_outlines"] == 2
    assert pending["suggested_kind"] is None  # el nombre no dice nada: decide el usuario
    assert review["summary"]["pending_elements"] == 2

    result = reconvert(server, job, {"adjust": {"SECTOR-B": {"kind": "building"}}})
    sector = result["report"]["layers"]["SECTOR-B"]
    assert sector["status"] == "converted" and sector["objects"] == 2
    assert sector["heights"] == {"default_height": 2}  # altura provisional, así informada
    assert sector["ground_check"]["floating"] == 0
    assert result["report"]["pending_layers"] == []

    # Decidir «ignorar» también resuelve la capa: deja de proponerse
    result = reconvert(server, job, {"adjust": {"SECTOR-B": {"kind": "ignore"}}})
    assert result["report"]["layers"]["SECTOR-B"]["status"] == "ignored"
    assert result["report"]["summary"]["pending_layers"] == 0


def test_previous_result_can_be_recovered_after_a_repair(server: str, tmp_path: Path) -> None:
    job = old_project(server, tmp_path, legacy_files=False)
    before = model(server, job)
    # Materiales/ediciones confirmados viven en out/: aquí, un archivo testigo
    witness = tmp_path / "jobs" / job / "out" / "project.json"
    witness.write_text('{"marca": "revisiones del usuario"}', encoding="utf-8")
    yaml_before = (tmp_path / "jobs" / job / "config.yaml").read_text(encoding="utf-8")

    reconvert(server, job, {"adjust": {"BLOQUES": {"support": "auto"}}})
    after = model(server, job)
    assert not witness.exists()  # el resultado nuevo parte del plano

    assert post_json(f"{server}/api/jobs/{job}/restore", {})[0] == 200
    assert revision(model(server, job)) == revision(before)
    assert witness.read_text(encoding="utf-8") == '{"marca": "revisiones del usuario"}'
    assert (tmp_path / "jobs" / job / "config.yaml").read_text(encoding="utf-8") == yaml_before
    _, result = call(f"{server}/api/jobs/{job}/result")
    assert result["revision"] == revision(before)

    # Recuperar también se puede deshacer
    assert post_json(f"{server}/api/jobs/{job}/restore", {})[0] == 200
    assert revision(model(server, job)) == revision(after)


def test_failed_repair_keeps_model_config_and_report_consistent(
    server: str, tmp_path: Path
) -> None:
    job = old_project(server, tmp_path, legacy_files=False)
    job_dir = tmp_path / "jobs" / job
    before = model(server, job)
    source = job_dir / "input.dxf"
    original = source.read_bytes()
    source.write_bytes(b"plano corrupto")
    code, accepted = post_json(
        f"{server}/api/jobs/{job}/reconvert", {"adjust": {"BLOQUES": {"support": "auto"}}}
    )
    assert code == 202
    final, _ = poll(server, accepted["status"])
    assert final["state"] == "failed"
    source.write_bytes(original)
    assert revision(model(server, job)) == revision(before)
    code, result = call(f"{server}/api/jobs/{job}/result")
    assert result["revision"] == revision(before)
    assert current_adjustments((job_dir / "config.yaml").read_text(encoding="utf-8")) == {}
    code, review = call(f"{server}/api/jobs/{job}/review")
    assert review["canRestore"] is False and review["revision"] == revision(before)
    assert post_json(f"{server}/api/jobs/{job}/restore", {})[0] == 409


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"adjust": {"BLOQUES": {"support": "terreno"}}},
        {"adjust": {"BLOQUES": {"kind": "rascacielos"}}},
        {"adjust": {"CURVAS": {"support": "auto"}}},
        {"adjust": {"BLOQUES": {"otra": 1}}},
        {"heights": {"BLOQUES": -3}},
    ],
)
def test_invalid_adjustments_change_nothing(server: str, tmp_path: Path, body: Any) -> None:
    job = old_project(server, tmp_path, legacy_files=False)
    before = revision(model(server, job))
    code, _ = post_json(f"{server}/api/jobs/{job}/reconvert", body)
    assert code in (400, 422)
    assert revision(model(server, job)) == before


# --- Identidad de la ejecución ------------------------------------------------------------------


def test_health_identifies_the_running_code(server: str) -> None:
    code, health = call(f"{server}/api/health")
    build = health["build"]
    assert code == 200 and build["id"] == build_info.LOADED[:12]
    assert build["stale"] is False and build["source"] == build["id"]
    assert build["pid"] > 0 and build["started"].startswith("20")


def test_code_changed_on_disk_after_start_is_detected(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "a.py").write_text("x = 1\n", encoding="utf-8")
    loaded = build_info.source_fingerprint(package)
    assert build_info.source_fingerprint(package) == loaded
    (package / "a.py").write_bytes(b"x = 1\r\n")  # solo cambia el final de línea: mismo código
    assert build_info.source_fingerprint(package) == loaded
    (package / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert build_info.source_fingerprint(package) != loaded
    (package / "b.py").write_text("", encoding="utf-8")
    assert build_info.source_fingerprint(package) != loaded


def test_hand_written_config_keeps_its_elevation_heights_when_repaired(
    server: str, tmp_path: Path
) -> None:
    """En una configuración del usuario, `height_from: elevation` es su decisión: reparar el
    apoyo la conserva y la cota Z pasa a ser la cubierta absoluta sobre la base real."""
    doc = ezdxf.new("R2018")
    doc.units = 6
    msp = doc.modelspace()
    for x in range(0, 101, 10):
        msp.add_lwpolyline(
            [(x, 0), (x, 100)], dxfattribs={"layer": "CURVAS", "elevation": 100 + x / 5}
        )
    msp.add_lwpolyline(
        [(10, 10), (20, 10), (20, 20), (10, 20)],
        close=True,
        dxfattribs={"layer": "BLOQUES", "elevation": 125.0},  # cubierta a la cota 125
    )
    path = tmp_path / "manual.dxf"
    doc.saveas(path)
    manual = (
        "layers:\n  - match: BLOQUES\n    kind: building\n    height_from: elevation\n"
        "  - match: CURVAS\n    kind: terrain\n"
    )
    headers = {CONFIG_HEADER: base64.b64encode(manual.encode()).decode()}
    code, accepted = call(
        f"{server}/api/convert?name=manual.dxf&async=1", path.read_bytes(), headers
    )
    assert code == 202
    assert poll(server, accepted["status"])[0]["state"] == "done"
    job = accepted["job"]
    before = layer_extras(model(server, job), "BLOQUES")
    assert before["objects"]["base"] == [0.0] and before["objects"]["height"] == [125.0]
    assert before["ground_check"]["buried"] == 0 and before["ground_check"]["intersecting"] == 1

    result = reconvert(server, job, {"adjust": {"BLOQUES": {"support": "auto"}}})
    after = layer_extras(model(server, job), "BLOQUES")
    assert after["objects"]["base"] == [104.0]  # planta en la cota alta del terreno
    assert after["objects"]["height"] == [21.0]  # 125 - 104: cubierta absoluta conservada
    assert after["objects"]["height_origin"] == ["elevation"]
    assert after["objects"]["height_confidence"] == ["inferred"]
    assert result["report"]["summary"]["inferred_heights"] == 1
    text = (tmp_path / "jobs" / job / "config.yaml").read_text(encoding="utf-8")
    assert current_adjustments(text) == {"BLOQUES": {"support": "auto"}}
