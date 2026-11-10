from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from extrude.build import build_scene
from extrude.cli import USER_ERROR_EXIT, app
from extrude.config import HeightSource, Kind, parse_config
from extrude.dxf_reader import read_dxf
from extrude.init_config import _guess_kind, draft_config


def draft(samples_dir: Path, name: str) -> tuple[str, dict[str, object]]:
    text = draft_config(read_dxf(samples_dir / name))
    config = parse_config(yaml.safe_load(text))
    return text, {r.match: r for r in config.layers}


def test_block_draft_detects_floor_texts(samples_dir: Path) -> None:
    text, rules = draft(samples_dir, "block_2d.dxf")
    edif = rules["EDIFICIOS"]
    assert edif.kind is Kind.BUILDING  # type: ignore[attr-defined]
    assert edif.height_from is HeightSource.TEXT  # type: ignore[attr-defined]
    assert edif.text_layers == ["EDIFICIOS_TEXTO"]  # type: ignore[attr-defined]
    assert rules["VIALES"].kind is Kind.ROAD  # type: ignore[attr-defined]
    assert rules["AGUA"].kind is Kind.WATER  # type: ignore[attr-defined]
    assert "4P | 6P | 3P | 2P" in text


def test_block_draft_produces_correct_heights(samples_dir: Path) -> None:
    read = read_dxf(samples_dir / "block_2d.dxf")
    config = parse_config(yaml.safe_load(draft_config(read)))
    heights = [o.height for o in build_scene(read, config).layers["EDIFICIOS"].objects]
    assert heights == [12.0, 18.0, 9.0, 6.0]


def test_nested_draft_detects_attribute(samples_dir: Path) -> None:
    _, rules = draft(samples_dir, "nested_blocks.dxf")
    edif = rules["EDIFICIOS"]
    assert edif.height_from is HeightSource.ATTRIBUTE  # type: ignore[attr-defined]
    assert edif.attribute == "PLANTAS"  # type: ignore[attr-defined]
    assert rules["VEGETACION"].kind is Kind.VEGETATION  # type: ignore[attr-defined]
    assert rules["MOBILIARIO"].kind is Kind.IGNORE  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("layer", "kind"),
    [
        ("EDIFICACIÓN_ALTA", Kind.BUILDING),
        ("ZONAS_VERDES", Kind.VEGETATION),
        ("CURVAS NIVEL", Kind.TERRAIN),
        ("MOBILIARIO", Kind.IGNORE),
        ("A-ROAD-CNTR", Kind.ROAD),
    ],
)
def test_guess_kind(layer: str, kind: Kind) -> None:
    assert _guess_kind(layer) is kind


def test_glob_characters_in_layer_names_are_escaped() -> None:
    from extrude.init_config import _glob_escape

    rule = parse_config({"layers": [{"match": _glob_escape("EDIF[1]*"), "kind": "building"}]})
    assert rule.rule_for("EDIF[1]*") is not None
    assert rule.rule_for("EDIF1X") is None


def test_cli_writes_file_and_refuses_overwrite(samples_dir: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    out = tmp_path / "proyecto.yaml"
    args = ["init-config", str(samples_dir / "block_2d.dxf"), "-o", str(out)]
    assert runner.invoke(app, args).exit_code == 0
    assert out.read_text(encoding="utf-8").startswith("# Borrador")
    again = runner.invoke(app, args)
    assert again.exit_code == USER_ERROR_EXIT
    assert "--force" in again.output
    assert runner.invoke(app, [*args, "--force"]).exit_code == 0
