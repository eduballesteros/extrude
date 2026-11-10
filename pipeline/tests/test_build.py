"""Aceptación de la Fase 2 sobre los fixtures completos."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import ezdxf
import numpy as np
import pytest
import sample_dxf as s
import trimesh
from shapely.geometry import Point, Polygon

from extrude.build import BuildResult, build_scene
from extrude.config import Config, parse_config
from extrude.dxf_reader import read_dxf
from extrude.extrusion import triangle_areas
from extrude.heights import HeightOrigin

EXAMPLE = Path(__file__).parents[1] / "config" / "example.yaml"
BULGE_SEGMENT_AREA = 39.3101  # segmento circular: bulge 0.5, cuerda 15 m


def rules(*layers: dict[str, Any]) -> Config:
    return parse_config({"layers": [*layers, {"match": "*", "kind": "ignore"}]})


BUILDINGS_FROM_TEXT = rules(
    {
        "match": "EDIFICIOS",
        "kind": "building",
        "height_from": "text",
        "text_layers": ["EDIFICIOS_TEXTO"],
        "default_height": 99,
        "bottom_cap": True,
    },
    {"match": "VIALES", "kind": "road", "height": 0.05},
    {"match": "AGUA", "kind": "water"},
)


@pytest.fixture(scope="module")
def block(samples_dir: Path) -> BuildResult:
    return build_scene(read_dxf(samples_dir / "block_2d.dxf"), BUILDINGS_FROM_TEXT)


def solids(result: BuildResult, layer: str) -> list[trimesh.Trimesh]:
    meshes = []
    for obj in result.layers[layer].objects:
        tm = trimesh.Trimesh(obj.mesh.vertices, obj.mesh.faces, process=False)
        tm.merge_vertices()
        meshes.append(tm)
    return meshes


def test_heights_from_floor_texts(block: BuildResult) -> None:
    objects = block.layers["EDIFICIOS"].objects
    assert [o.height for o in objects] == [b.floors * 3.0 for b in s.BUILDINGS]
    assert {o.height_origin for o in objects} == {HeightOrigin.TEXT}


def test_building_volumes(block: BuildResult) -> None:
    volumes = [m.volume for m in solids(block, "EDIFICIOS")]
    expected = [
        20 * 12 * 12,
        20 * 15 * 18,
        (30 * 20 - 10 * 10) * 9,  # patio descontado
        (15 * 15 + BULGE_SEGMENT_AREA) * 6,
    ]
    assert volumes == pytest.approx(expected, rel=2e-3)


def test_watertight_outward_and_no_degenerate(block: BuildResult) -> None:
    for mesh in solids(block, "EDIFICIOS"):
        assert mesh.is_watertight
        assert mesh.is_winding_consistent
        assert mesh.volume > 0
    for obj in block.layers["EDIFICIOS"].objects:
        assert triangle_areas(obj.mesh.vertices, obj.mesh.faces).min() > 1e-6
    assert block.layers["EDIFICIOS"].stats.degenerate_triangles == 0


def test_patio_is_a_hole(block: BuildResult) -> None:
    assert block.layers["EDIFICIOS"].stats.topology.holes == 1
    building = solids(block, "EDIFICIOS")[2]
    patio = Polygon([(10, 25), (20, 25), (20, 35), (10, 35)])
    assert not any(patio.contains(Point(c[:2])) for c in building.triangles_center)


def test_road_and_water_heights(block: BuildResult) -> None:
    (road,) = block.layers["VIALES"].objects
    assert road.height == 0.05
    (water,) = block.layers["AGUA"].objects
    assert water.height == 0
    assert np.allclose(water.mesh.vertices[:, 2], 0)


def test_text_only_layers_are_reported_not_built(block: BuildResult) -> None:
    assert "EDIFICIOS_TEXTO" in block.ignored
    assert "TEXTOS" in block.ignored


def test_utm_fixture_gives_same_geometry(samples_dir: Path, block: BuildResult) -> None:
    utm = build_scene(read_dxf(samples_dir / "block_2d_utm.dxf"), BUILDINGS_FROM_TEXT)
    offset = np.array([*s.UTM_OFFSET, 0])
    for a, b in zip(
        block.layers["EDIFICIOS"].objects, utm.layers["EDIFICIOS"].objects, strict=True
    ):
        assert a.height == b.height
        np.testing.assert_allclose(b.mesh.vertices - offset, a.mesh.vertices, atol=1e-6)


def test_heights_from_block_attribute(samples_dir: Path) -> None:
    config = rules(
        {
            "match": "EDIFICIOS",
            "kind": "building",
            "height_from": "attribute",
            "attribute": "plantas",
        }
    )
    result = build_scene(read_dxf(samples_dir / "nested_blocks.dxf"), config)
    heights = sorted(o.height for o in result.layers["EDIFICIOS"].objects)
    assert heights == [f * 3.0 for f in s.TYPICAL_BUILDING_FLOORS]


def test_fallback_chain_and_missing_height(samples_dir: Path) -> None:
    read = read_dxf(samples_dir / "block_2d.dxf")
    no_text = rules(
        {
            "match": "EDIFICIOS",
            "kind": "building",
            "height_from": "text",
            "text_layers": ["NADA"],
            "floors": 2,
            "floor_height": 3.5,
        }
    )
    objs = build_scene(read, no_text).layers["EDIFICIOS"].objects
    assert {(o.height, o.height_origin) for o in objs} == {(7.0, HeightOrigin.FLOORS)}

    missing = build_scene(read, rules({"match": "EDIFICIOS", "kind": "building"}))
    stats = missing.layers["EDIFICIOS"].stats
    assert stats.dropped_without_height == len(s.BUILDINGS)
    assert stats.heights[HeightOrigin.MISSING] == len(s.BUILDINGS)


def _labelled_square(tmp_path: Path, label: str) -> Path:
    doc = ezdxf.new("R2018")
    doc.units = 6
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "E"})
    msp.add_text(label, dxfattribs={"layer": "T", "insert": (4, 5)})
    path = tmp_path / "label.dxf"
    doc.saveas(path)
    return path


@pytest.mark.parametrize(
    ("label", "rule", "expected"),
    [
        ("B+3", {"text_regex": r"B\s*\+\s*(\d+)", "floor_offset": 1}, 12.0),
        ("IV", {"text_regex": r"^([IVX]+)$"}, 12.0),
        ("h=12,5m", {"text_regex": r"(\d+(?:[.,]\d+)?)\s*m", "value_means": "meters"}, 12.5),
    ],
)
def test_project_specific_text_conventions(
    tmp_path: Path, label: str, rule: dict[str, Any], expected: float
) -> None:
    config = rules({"match": "E", "kind": "building", "height_from": "text", **rule})
    result = build_scene(read_dxf(_labelled_square(tmp_path, label)), config)
    (obj,) = result.layers["E"].objects
    assert obj.height == pytest.approx(expected)


def test_elevation_source(tmp_path: Path) -> None:
    doc = ezdxf.new("R2018")
    doc.modelspace().add_lwpolyline(
        [(0, 0), (5, 0), (5, 5)], close=True, dxfattribs={"layer": "E", "elevation": 14.0}
    )
    path = tmp_path / "elev.dxf"
    doc.saveas(path)
    config = rules(
        {"match": "E", "kind": "building", "height_from": "elevation", "base_elevation": 2}
    )
    (obj,) = build_scene(read_dxf(path), config).layers["E"].objects
    assert obj.height == pytest.approx(12.0)
    assert obj.mesh.vertices[:, 2].max() == pytest.approx(14.0)


def test_terrain_tin_and_fallback_plane(tmp_path: Path, samples_dir: Path) -> None:
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    for z in (0, 5, 10):
        msp.add_lwpolyline(
            [(0, z * 2), (50, z * 2)], dxfattribs={"layer": "CURVAS", "elevation": z}
        )
    path = tmp_path / "terrain.dxf"
    doc.saveas(path)
    (tin,) = (
        build_scene(read_dxf(path), rules({"match": "CURVAS", "kind": "terrain"}))
        .layers["CURVAS"]
        .objects
    )
    assert tin.mesh.vertices[:, 2].max() == 10
    up = trimesh.Trimesh(tin.mesh.vertices, tin.mesh.faces, process=False).face_normals[:, 2]
    assert (up > 0).all()

    flat = build_scene(
        read_dxf(samples_dir / "block_2d.dxf"), rules({"match": "AGUA", "kind": "terrain"})
    )
    (plane,) = flat.layers["AGUA"].objects
    assert plane.mesh.triangle_count == 2


def test_existing_3d_meshes_are_kept_in_metres(samples_dir: Path) -> None:
    config = rules({"match": "CUBO", "kind": "building"}, {"match": "MALLA", "kind": "building"})
    result = build_scene(read_dxf(samples_dir / "faces_3d.dxf"), config)
    cube = trimesh.util.concatenate(
        [trimesh.Trimesh(o.mesh.vertices, o.mesh.faces) for o in result.layers["CUBO"].objects]
    )
    cube.merge_vertices()
    assert cube.is_watertight
    assert abs(cube.volume) == pytest.approx(1000.0)  # 10 m de lado
    assert result.layers["MALLA"].stats.triangles == 2 + 4  # base quad + 4 caras


def test_unruled_layers_warn(samples_dir: Path) -> None:
    config = parse_config({"layers": [{"match": "EDIFICIOS", "kind": "building", "height": 3}]})
    result = build_scene(read_dxf(samples_dir / "block_2d.dxf"), config)
    assert set(result.unruled) == {"AGUA", "VIALES", "EDIFICIOS_TEXTO", "TEXTOS"}
    assert any("sin regla" in w for w in result.warnings)


def test_example_config_on_block(samples_dir: Path) -> None:
    from extrude.config import load_config

    result = build_scene(read_dxf(samples_dir / "block_2d.dxf"), load_config(EXAMPLE))
    assert result.triangle_count > 0
    assert len(result.layers["EDIFICIOS"].objects) == len(s.BUILDINGS)
