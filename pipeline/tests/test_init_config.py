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


def test_draft_seats_layers_without_elevation_on_terrain(tmp_path: Path) -> None:
    import ezdxf

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    for y in range(0, 50, 10):
        msp.add_lwpolyline([(0, y), (50, y)], dxfattribs={"layer": "CURVAS", "elevation": y})
    msp.add_lwpolyline([(5, 5), (9, 5), (9, 9)], close=True, dxfattribs={"layer": "ZONA VERDE"})
    msp.add_lwpolyline([(20, 20), (30, 20), (30, 30)], close=True, dxfattribs={"layer": "EDIF"})
    path = tmp_path / "t.dxf"
    doc.saveas(path)
    config = parse_config(yaml.safe_load(draft_config(read_dxf(path))))
    by = {r.match: r for r in config.layers}
    assert by["ZONA VERDE"].base_auto
    assert by["EDIF"].base_auto
    assert not by["CURVAS"].base_auto


def test_draft_with_contours_only_seats_buildings_on_terrain(tmp_path: Path) -> None:
    """Curvas de nivel acotadas (Z = 50 y 100), sin puntos acotados, y edificios a Z = 0."""
    import ezdxf

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    for i, z in enumerate((50.0, 100.0)):
        msp.add_lwpolyline(
            [(0, i * 20), (60, i * 20 + 5)], dxfattribs={"layer": "CURVAS", "elevation": z}
        )
    msp.add_lwpolyline(
        [(10, 5), (20, 5), (20, 15), (10, 15)], close=True, dxfattribs={"layer": "EDIF"}
    )
    path = tmp_path / "curvas.dxf"
    doc.saveas(path)
    text = draft_config(read_dxf(path))
    rules_by_match = {r.match: r for r in parse_config(yaml.safe_load(text)).layers}
    assert rules_by_match["CURVAS"].kind is Kind.TERRAIN
    assert rules_by_match["EDIF"].kind is Kind.BUILDING
    assert rules_by_match["EDIF"].base_auto
    assert "base_auto: true" in text


@pytest.mark.parametrize("terrain_z", [-60.0, 20.0, None])
@pytest.mark.parametrize("building_z", [(0.0, 35.0), (35.0, 35.0)])
def test_draft_prefers_terrain_to_unverified_building_bases(
    tmp_path: Path, terrain_z: float | None, building_z: tuple[float, float]
) -> None:
    import ezdxf

    doc = ezdxf.new("R2018")
    doc.units = 6  # metros
    msp = doc.modelspace()
    if terrain_z is not None:
        for y in (0, 40):
            msp.add_lwpolyline(
                [(0, y), (40, y)],
                dxfattribs={"layer": "CURVAS", "elevation": terrain_z + y / 10},
            )
    for x, z in zip((5, 20), building_z, strict=True):
        msp.add_lwpolyline(
            [(x, 5), (x + 5, 5), (x + 5, 10), (x, 10)],
            close=True,
            dxfattribs={"layer": "-EDIFICIOS", "elevation": z},
        )
        msp.add_text("4P", dxfattribs={"layer": "ROTULOS", "insert": (x + 2, 7)})
    path = tmp_path / "bases.dxf"
    doc.saveas(path)
    read = read_dxf(path)
    config = parse_config(yaml.safe_load(draft_config(read)))
    rule = config.rule_for("-EDIFICIOS")
    assert rule is not None
    assert rule.height_from is HeightSource.TEXT
    # La base no se fija en el borrador: la decide la política automática al convertir
    assert rule.base_auto and not rule.base_from_terrain and not rule.base_from_elevation
    origins = build_scene(read, config).layers["-EDIFICIOS"].stats.base_origins
    if terrain_z is not None:
        assert set(origins) == {"terrain"}
    else:
        # Sin terreno: la Z de la huella (si la tiene) es una base inferida; si no, cota 0
        assert origins["source_elevation_inferred"] == sum(z != 0 for z in building_z)
    geometry = build_scene(read, config).layers["-EDIFICIOS"]
    assert len(geometry.objects) == 2
    assert [obj.height for obj in geometry.objects] == [12.0, 12.0]
    bases = sorted(float(obj.mesh.vertices[:, 2].min()) for obj in geometry.objects)
    if terrain_z is None:
        assert bases == sorted(building_z)
    else:
        assert bases == pytest.approx([terrain_z + 0.5] * 2)
        assert geometry.stats.ground is not None
        assert geometry.stats.ground.checked == 2
        assert geometry.stats.ground.floating == 0
        assert geometry.stats.ground.buried == 0


@pytest.mark.parametrize("terrain_z", [None, -60.0, 20.0])
@pytest.mark.parametrize("building_z", [-15.0, 35.0])
def test_draft_uses_provisional_height_and_terrain_for_unverified_z(
    tmp_path: Path, terrain_z: float | None, building_z: float
) -> None:
    import ezdxf

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    doc.units = 6
    if terrain_z is not None:
        for y in (0, 40):
            msp.add_lwpolyline(
                [(0, y), (40, y)],
                dxfattribs={"layer": "CURVAS", "elevation": terrain_z + y / 10},
            )
    msp.add_lwpolyline(
        [(5, 5), (10, 5), (10, 10), (5, 10)],
        close=True,
        dxfattribs={"layer": "_BLOQUES LINEALES", "elevation": building_z},
    )
    path = tmp_path / "coronacion.dxf"
    doc.saveas(path)
    read = read_dxf(path)
    text = draft_config(read)
    config = parse_config(yaml.safe_load(text))
    rule = config.rule_for("_BLOQUES LINEALES")
    assert rule is not None
    assert rule.height_from is (HeightSource.ELEVATION if terrain_z is None else None)
    assert rule.base_auto and not rule.base_from_terrain
    assert not rule.base_from_elevation
    geometry = build_scene(read, config).layers["_BLOQUES LINEALES"]
    assert len(geometry.objects) == 1
    if terrain_z is not None:
        assert "default_height PROVISIONAL" in text
        obj = geometry.objects[0]
        assert obj.height == 9.0
        assert obj.floor_elevation == pytest.approx(terrain_z + 1.0)
        assert float(obj.mesh.vertices[:, 2].min()) == pytest.approx(terrain_z + 0.5)
        assert geometry.stats.ground is not None
        assert geometry.stats.ground.floating == 0
        assert geometry.stats.ground.buried == 0
