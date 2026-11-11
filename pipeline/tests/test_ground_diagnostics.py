"""Evidencia para revisar el apoyo sin atribuir los recuentos al archivo de origen."""

from pathlib import Path

import ezdxf
import pytest

from extrude.config import parse_config
from extrude.convert import ConvertOptions, convert


@pytest.mark.parametrize("as_block", [False, True])
def test_report_identifies_partially_buried_object_and_applied_base(
    tmp_path: Path,
    as_block: bool,
) -> None:
    doc = ezdxf.new()
    doc.units = 6
    msp = doc.modelspace()
    for x, y in ((0, 0), (10, 0), (0, 10), (10, 10)):
        msp.add_point((x, y, x * 2), dxfattribs={"layer": "T"})
    space = doc.blocks.new("EQUIP") if as_block else msp
    outline = space.add_lwpolyline(
        [(0, 0), (10, 0), (10, 10), (0, 10)],
        close=True,
        dxfattribs={"layer": "E", "elevation": 5},
    )
    insert = msp.add_blockref("EQUIP", (0, 0, 0)) if as_block else None
    source = tmp_path / "slope.dxf"
    doc.saveas(source)
    config = parse_config(
        {
            "layers": [
                {"match": "T", "kind": "terrain"},
                {
                    "match": "E",
                    "kind": "building",
                    "base_from_terrain": True,
                    "terrain_support": "minimum",
                    "default_height": 9,
                },
            ]
        }
    )
    result = convert(source, ConvertOptions(config, tmp_path / "out"))
    (detail,) = result.report["layers"]["E"]["ground_details"]
    assert detail["category"] == "partially_buried"
    if insert is not None:
        assert detail["insert_handle"] == insert.dxf.handle
        assert detail["block"] == "EQUIP"
    else:
        assert detail["handles"] == [outline.dxf.handle]
    assert detail["source_elevation_m"] == 5
    assert detail["base_m"] == detail["terrain_min_m"] == 0
    assert detail["top_m"] == detail["height_m"] == 9
    assert detail["terrain_max_m"] == 20
    assert detail["base_mode"] == "terrain_min"
    assert detail["height_origin"] == "default_height"
    assert detail["terrain_covered"] is True
    markdown = (tmp_path / "out" / "report.md").read_text("utf-8")
    assert "Objetos que revisar" in markdown
    assert (insert.dxf.handle if insert is not None else outline.dxf.handle) in markdown
    assert "Enterrado parcialmente" in markdown
    assert any("parcialmente enterrados" in w for w in result.report["warnings"])


def test_diagnostic_marks_estimated_terrain_outside_tin() -> None:
    import numpy as np
    from shapely.geometry import box

    from extrude.terrain import TerrainSurface

    terrain = TerrainSurface(np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 10.0, 10.0]]))
    assert terrain.covers(box(1, 1, 2, 2))
    assert not terrain.covers(box(8, 8, 12, 12))


@pytest.mark.parametrize("origin", [(20, 20), (4, 4), (1, 1), (0, 0)])
def test_report_records_coverage_even_when_support_looks_correct(
    tmp_path: Path, origin: tuple[int, int]
) -> None:
    doc = ezdxf.new()
    doc.units = 6
    msp = doc.modelspace()
    for p in [(0, 0, 5), (10, 0, 5), (0, 10, 5)]:
        msp.add_point(p, dxfattribs={"layer": "T"})
    x, y = origin
    msp.add_lwpolyline(
        [(x, y), (x + 5, y), (x + 5, y + 5), (x, y + 5)], close=True, dxfattribs={"layer": "E"}
    )
    path = tmp_path / "coverage.dxf"
    doc.saveas(path)
    config = parse_config(
        {
            "layers": [
                {"match": "T", "kind": "terrain"},
                {"match": "E", "kind": "building", "height": 3, "base_from_terrain": True},
            ]
        }
    )
    result = convert(path, ConvertOptions(config, tmp_path / "out"))
    layer = result.report["layers"]["E"]
    (detail,) = layer["ground_details"]
    covered = origin == (0, 0)
    assert detail["terrain_covered"] is covered
    assert detail["category"] == "supported"
    assert layer["ground_check"]["floating"] == 0
    assert layer["terrain_coverage"]["extrapolated"] == int(not covered)
    assert any("salen del TIN" in warning for warning in result.report["warnings"]) is not covered
