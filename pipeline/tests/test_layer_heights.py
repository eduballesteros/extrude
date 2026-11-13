"""Altura por capa desde el visor: edición del YAML y reconversión del proyecto en el servidor."""

from __future__ import annotations

import base64
import json
import struct
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_server_async import CONFIGS, call, poll, post_async

from extrude.config import ConfigError, parse_config
from extrude.layer_heights import (
    BLOCK_START,
    HeightError,
    apply_heights,
    current_heights,
    describe,
    exact_pattern,
)
from extrude.server import create_server

SAMPLES = Path(__file__).parents[2] / "samples"
YAML = (CONFIGS / "block_2d.yaml").read_text(encoding="utf-8")


def load(text: str) -> Any:
    import yaml

    return parse_config(yaml.safe_load(text))


def test_fixing_a_height_keeps_the_file_and_wins_over_the_original_rule() -> None:
    edited = apply_heights(YAML, {"EDIFICIOS": 21.5})
    assert BLOCK_START in edited
    # Lo escrito a mano (comentarios incluidos) sigue igual, en el mismo orden
    assert [line for line in edited.splitlines() if "extrude-visor" not in line][:8] == (
        YAML.splitlines()[:8]
    )
    assert "height_from: text" in edited  # la regla original no se toca
    rule = load(edited).rule_for("EDIFICIOS")
    assert rule is not None and rule.height == 21.5 and rule.height_from is None
    assert rule.match == "EDIFICIOS" and rule.kind.value == "building"
    assert current_heights(edited) == {"EDIFICIOS": 21.5}


def test_changing_and_removing_a_height_is_idempotent_and_reversible() -> None:
    once = apply_heights(YAML, {"EDIFICIOS": 21.5})
    twice = apply_heights(once, {"EDIFICIOS": 9, "VIALES": 0.2})
    assert twice.count(BLOCK_START) == 1
    assert current_heights(twice) == {"EDIFICIOS": 9.0, "VIALES": 0.2}
    assert apply_heights(twice, {"VIALES": None}) == apply_heights(YAML, {"EDIFICIOS": 9})
    assert apply_heights(twice, {"EDIFICIOS": None, "VIALES": None}) == YAML


def test_describe_tells_where_each_height_comes_from() -> None:
    edited = apply_heights(YAML, {"EDIFICIOS": 12})
    by_name = {entry.name: entry for entry in describe(edited, ["EDIFICIOS", "VIALES", "OTRA"])}
    assert by_name["EDIFICIOS"].fixed == 12 and by_name["EDIFICIOS"].source == "viewer"
    assert by_name["VIALES"].editable and by_name["VIALES"].fixed is None
    assert by_name["VIALES"].rule_height == 0.05 and by_name["VIALES"].source == "fixed"
    assert not by_name["OTRA"].editable and by_name["OTRA"].reason
    original = {entry.name: entry for entry in describe(YAML, ["EDIFICIOS"])}
    assert original["EDIFICIOS"].source == "text"


@pytest.mark.parametrize("value", [0, -3, 1001, float("nan"), float("inf"), "12", None, True])
def test_invalid_heights_are_rejected(value: object) -> None:
    if value is None:
        assert apply_heights(YAML, {"EDIFICIOS": None}) == YAML  # quitar lo que no hay: nada
        return
    with pytest.raises(HeightError):
        apply_heights(YAML, {"EDIFICIOS": value})  # type: ignore[dict-item]


def test_layers_that_are_not_extruded_cannot_get_a_height() -> None:
    with pytest.raises(HeightError):
        apply_heights(YAML, {"CAPA_INEXISTENTE": 10})
    with pytest.raises(ConfigError):
        apply_heights("layers: [1, 2", {"EDIFICIOS": 10})


def test_layer_names_with_glob_characters_match_exactly() -> None:
    text = 'layers:\n  - match: "*"\n    kind: building\n    height: 3\n'
    edited = apply_heights(text, {"A[1]*": 7})
    config = load(edited)
    assert config.rule_for("A[1]*").height == 7  # type: ignore[union-attr]
    assert config.rule_for("A1X").height == 3  # type: ignore[union-attr]
    assert exact_pattern("A[1]*?") == "A[[]1][*][?]"


def test_inline_layers_are_rewritten_when_the_block_cannot_be_inserted() -> None:
    text = "units: m\nlayers: [{match: 'ED*', kind: building, height: 3}]\n"
    edited = apply_heights(text, {"EDIF": 8})
    assert load(edited).rule_for("EDIF").height == 8  # type: ignore[union-attr]
    assert load(edited).rule_for("EDX").height == 3  # type: ignore[union-attr]


# --- Servidor: reconversión del mismo proyecto -------------------------------------------------


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


def glb_json(data: bytes) -> dict[str, Any]:
    length = struct.unpack("<I", data[12:16])[0]
    parsed: dict[str, Any] = json.loads(data[20 : 20 + length])
    return parsed


def heights_of(base: str, job: str) -> set[float]:
    import urllib.request

    with urllib.request.urlopen(f"{base}/api/jobs/{job}/model.glb") as resp:  # noqa: S310
        gltf = glb_json(resp.read())
    found: set[float] = set()
    for node in gltf["nodes"]:
        extras = node.get("extras") or {}
        if extras.get("layer") == "EDIFICIOS":
            found.update(extras["objects"]["height"])
    return found


def convert(base: str) -> str:
    code, accepted = post_async(base, SAMPLES / "block_2d.dxf", config=CONFIGS / "block_2d.yaml")
    assert code == 202, accepted
    final, _ = poll(base, accepted["status"])
    assert final["state"] == "done", final
    job: str = accepted["job"]
    return job


def test_reconvert_updates_yaml_geometry_and_result(server: str) -> None:
    job = convert(server)
    before = heights_of(server, job)
    assert len(before) > 1  # alturas distintas leídas de los rótulos

    code, info = call(f"{server}/api/jobs/{job}/layers-config")
    assert code == 200 and info["repeatable"] is True and info["heights"] == {}
    entry = next(e for e in info["layers"] if e["name"] == "EDIFICIOS")
    assert entry["editable"] and entry["source"] == "text"

    code, accepted = post_json(
        f"{server}/api/jobs/{job}/reconvert", {"heights": {"EDIFICIOS": 21.5}}
    )
    assert code == 202, accepted
    final, _ = poll(server, accepted["status"])
    assert final["state"] == "done", final
    assert final["result"]["job"] == job and final["result"]["config_generated"] is False

    assert heights_of(server, job) == {21.5}  # geometría nueva de verdad
    code, info = call(f"{server}/api/jobs/{job}/layers-config")
    assert info["heights"] == {"EDIFICIOS": 21.5}
    import urllib.request

    with urllib.request.urlopen(f"{server}/api/jobs/{job}/config.yaml") as resp:  # noqa: S310
        text = resp.read().decode("utf-8")
    assert "height: 21.5" in text and BLOCK_START in text  # el YAML descargable es el usado

    # Reabrir el proyecto: el resultado guardado es el de la última conversión
    code, result = call(f"{server}/api/jobs/{job}/result")
    assert code == 200 and result["files"]["glb"].endswith("/model.glb")

    # Quitar la altura fijada devuelve las alturas originales
    code, accepted = post_json(
        f"{server}/api/jobs/{job}/reconvert", {"heights": {"EDIFICIOS": None}}
    )
    assert code == 202
    assert poll(server, accepted["status"])[0]["state"] == "done"
    assert heights_of(server, job) == before


def test_failed_or_invalid_reconversion_keeps_the_previous_model(
    server: str, tmp_path: Path
) -> None:
    job = convert(server)
    before = heights_of(server, job)
    code, error = post_json(f"{server}/api/jobs/{job}/reconvert", {"heights": {"EDIFICIOS": -1}})
    assert code == 422 and "EDIFICIOS" in error["error"]
    code, error = post_json(f"{server}/api/jobs/{job}/reconvert", {"heights": {"NO_EXISTE": 5}})
    assert code == 422
    assert post_json(f"{server}/api/jobs/{job}/reconvert", {})[0] == 400

    # La conversión falla a mitad (plano ilegible): el modelo y el YAML anteriores siguen ahí
    job_dir = tmp_path / "jobs" / job
    source = job_dir / "input.dxf"
    original = source.read_bytes()
    source.write_bytes(b"esto no es un DXF")
    code, accepted = post_json(f"{server}/api/jobs/{job}/reconvert", {"heights": {"EDIFICIOS": 30}})
    assert code == 202
    final, _ = poll(server, accepted["status"])
    assert final["state"] == "failed" and final["error"]
    source.write_bytes(original)
    assert heights_of(server, job) == before
    assert not (job_dir / "out.next").exists() and not (job_dir / "config.next.yaml").exists()
    assert call(f"{server}/api/jobs/{job}/layers-config")[1]["heights"] == {}


def test_models_without_a_drawing_explain_why_they_cannot_be_reconverted(server: str) -> None:
    job = convert(server)
    import urllib.request

    with urllib.request.urlopen(f"{server}/api/jobs/{job}/model.glb") as resp:  # noqa: S310
        glb = resp.read()
    code, made = call(f"{server}/api/models", glb)
    assert code == 201, made
    code, info = call(f"{server}/api/jobs/{made['job']}/layers-config")
    assert code == 200 and info["repeatable"] is False and "GLB" in info["reason"]
    code, error = post_json(
        f"{server}/api/jobs/{made['job']}/reconvert", {"heights": {"EDIFICIOS": 9}}
    )
    assert code == 409 and error["kind"] == "not_repeatable"
    assert call(f"{server}/api/jobs/{'0' * 32}/layers-config")[0] == 404


def test_reconvert_needs_json_from_an_allowed_origin(server: str) -> None:
    job = convert(server)
    url = f"{server}/api/jobs/{job}/reconvert"
    body = base64.b64encode(b"x")
    assert call(url, body, {"Content-Type": "text/plain"})[0] == 415
    assert (
        call(url, b"{}", {"Origin": "https://otro.example", "Content-Type": "application/json"})[0]
        == 403
    )
    time.sleep(0)


def test_reconvert_keeps_tiling_options(server: str) -> None:
    code, accepted = post_async(
        server, SAMPLES / "block_2d.dxf", "&tiles=50", config=CONFIGS / "block_2d.yaml"
    )
    assert code == 202, accepted
    first, _ = poll(server, accepted["status"])
    job = accepted["job"]
    assert first["result"]["files"]["manifest"]

    code, accepted = post_json(f"{server}/api/jobs/{job}/reconvert", {"heights": {"EDIFICIOS": 9}})
    assert code == 202, accepted
    final, _ = poll(server, accepted["status"])
    assert final["state"] == "done", final
    assert final["result"]["files"]["manifest"] and final["result"]["files"]["glb"] is None
    code, manifest = call(f"{server}/api/jobs/{job}/manifest.json")
    assert code == 200 and len(manifest["tiles"]) > 1
    import urllib.request

    heights: set[float] = set()
    for tile in manifest["tiles"]:
        url = f"{server}/api/jobs/{job}/{tile['file']}"
        with urllib.request.urlopen(url) as resp:  # noqa: S310
            for node in glb_json(resp.read())["nodes"]:
                extras = node.get("extras") or {}
                if extras.get("layer") == "EDIFICIOS":
                    heights.update(extras["objects"]["height"])
    assert heights == {9.0}  # todas las teselas se regeneraron con la altura nueva
