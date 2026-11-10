from __future__ import annotations

import json
from pathlib import Path

import pytest
from sample_dxf import SAMPLES
from typer.testing import CliRunner

from extrude.cli import USER_ERROR_EXIT, app

runner = CliRunner()


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_inspect_text_lists_layers_types_bbox_units(samples_dir: Path, name: str) -> None:
    result = runner.invoke(app, ["inspect", str(samples_dir / name)])
    assert result.exit_code == 0, result.output
    for heading in ("Unidades:", "BBox min:", "Entidades por tipo:", "Capas:"):
        assert heading in result.stdout


def test_inspect_json(samples_dir: Path) -> None:
    result = runner.invoke(app, ["inspect", str(samples_dir / "nested_blocks.dxf"), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    layers = {layer["name"]: layer for layer in data["layers"]}
    assert layers["EDIFICIOS"]["attribute_tags"] == ["PLANTAS"]
    assert data["totals"]["CIRCLE"] == 30


def test_inspect_warns_about_utm_and_acis(samples_dir: Path) -> None:
    utm = runner.invoke(app, ["inspect", str(samples_dir / "block_2d_utm.dxf")])
    assert "UTM" in utm.stdout
    acis = runner.invoke(app, ["inspect", str(samples_dir / "faces_3d.dxf"), "--json"])
    data = json.loads(acis.stdout)
    assert data["units"] == "mm"
    assert data["bbox_size_m"] == pytest.approx([50, 15, 10])
    assert any("CONVTOMESH" in w for w in data["warnings"])


def test_inspect_text_samples_help_write_regex(samples_dir: Path) -> None:
    result = runner.invoke(app, ["inspect", str(samples_dir / "block_2d.dxf"), "--json"])
    layers = {layer["name"]: layer for layer in json.loads(result.stdout)["layers"]}
    assert layers["EDIFICIOS_TEXTO"]["text_samples"] == ["4P", "6P", "3P", "2P"]


def test_inspect_user_error_exit_code(tmp_path: Path) -> None:
    result = runner.invoke(app, ["inspect", str(tmp_path / "missing.dxf")])
    assert result.exit_code == USER_ERROR_EXIT
    assert "Cómo arreglarlo" in result.output
