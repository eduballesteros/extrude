from __future__ import annotations

from pathlib import Path

import pytest

from extrude.config import ConfigError, Kind, load_config, parse_config
from extrude.heights import parse_number

EXAMPLE = Path(__file__).parents[1] / "config" / "example.yaml"


def test_example_config_loads() -> None:
    config = load_config(EXAMPLE)
    assert config.units_override is None
    rule = config.rule_for("edificios")
    assert rule is not None
    assert rule.kind is Kind.BUILDING
    assert rule.text_layer_matches("EDIFICIOS_TEXTO")
    assert not rule.text_layer_matches("TEXTOS")


def test_first_matching_rule_wins() -> None:
    config = parse_config(
        {
            "layers": [
                {"match": "EDIF_TXT", "kind": "ignore"},
                {"match": "EDIF*", "kind": "building"},
            ]
        }
    )
    assert config.rule_for("EDIF_TXT").kind is Kind.IGNORE  # type: ignore[union-attr]
    assert config.rule_for("EDIF_A").kind is Kind.BUILDING  # type: ignore[union-attr]
    assert config.rule_for("OTRA") is None


def test_default_material_per_kind() -> None:
    rule = parse_config({"layers": [{"match": "*", "kind": "water"}]}).layers[0]
    assert rule.effective_material.color == "#4a7fa8"


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ({"layers": [{"match": "A", "kind": "castle"}]}, "layers.0.kind"),
        ({"layers": [{"match": "A", "kind": "building", "text_regex": "(\\d+"}]}, "text_regex"),
        ({"layers": [{"match": "A", "kind": "building", "text_regex": "\\d+P"}]}, "grupo"),
        ({"layers": [{"match": "A", "kind": "building", "height_from": "attribute"}]}, "attribute"),
        ({"layers": [{"match": "A", "kind": "road", "material": {"color": "red"}}]}, "#rrggbb"),
        ({"layers": [{"match": "A", "kind": "road", "heigth": 2}]}, "heigth"),
        ({"tolerance": 0}, "tolerance"),
    ],
)
def test_invalid_config_is_actionable(raw: object, fragment: str) -> None:
    with pytest.raises(ConfigError) as info:
        parse_config(raw)
    assert fragment in str(info.value)
    assert "docs/CONFIG.md" in info.value.fix


def test_bad_yaml_syntax(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("layers:\n  - match: [\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="no es YAML válido"):
        load_config(path)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("4", 4.0), ("12,5", 12.5), ("IV", 4.0), ("ix", 9.0), ("XII", 12.0), ("abc", None)],
)
def test_parse_number(raw: str, expected: float | None) -> None:
    assert parse_number(raw) == expected
