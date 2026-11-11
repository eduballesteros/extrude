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


@pytest.mark.parametrize("source_kind", ["polyline", "hatch", "segments", "polyline_and_hatch"])
@pytest.mark.parametrize("elevation_mode", ["base", "height"])
def test_equal_xy_at_distinct_source_bases_remain_distinct_volumes(
    tmp_path: Path,
    source_kind: str,
    elevation_mode: str,
) -> None:
    doc = ezdxf.new()
    doc.units = 6
    msp = doc.modelspace()
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    for z in (0, 20) if elevation_mode == "base" else (3, 20):
        if source_kind in ("polyline", "polyline_and_hatch"):
            msp.add_lwpolyline(square, close=True, dxfattribs={"layer": "E", "elevation": z})
        if source_kind in ("hatch", "polyline_and_hatch"):
            hatch = msp.add_hatch(dxfattribs={"layer": "E", "elevation": (0, 0, z)})
            hatch.paths.add_polyline_path(square, is_closed=True)
        if source_kind == "segments":
            for a, b in zip(square, [*square[1:], square[0]], strict=True):
                msp.add_line((*a, z), (*b, z), dxfattribs={"layer": "E"})
    path = tmp_path / "stacked.dxf"
    doc.saveas(path)
    result = build_scene(
        read_dxf(path),
        rules(
            {
                "match": "E",
                "kind": "building",
                "detect_holes": False,
                **(
                    {"height": 3, "base_from_elevation": True}
                    if elevation_mode == "base"
                    else {"height_from": "elevation"}
                ),
            }
        ),
    )
    layer = result.layers["E"]
    assert sorted(
        (o.mesh.vertices[:, 2].min(), o.mesh.vertices[:, 2].max()) for o in layer.objects
    ) == ([(0, 3), (20, 23)] if elevation_mode == "base" else [(0, 3), (0, 20)])
    assert layer.stats.topology.duplicates_removed == (
        2 if source_kind == "polyline_and_hatch" else 0
    )


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


def _slope(tmp_path: Path) -> Path:
    """Curvas de nivel de una ladera (z = y / 2) con una mancha verde, un edificio y árboles
    (bloque) dibujados a cota 0."""
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    for y in range(0, 101, 10):
        msp.add_lwpolyline([(0, y), (100, y)], dxfattribs={"layer": "CURVAS", "elevation": y / 2})
    msp.add_lwpolyline(
        [(10, 10), (90, 10), (90, 90), (10, 90)], close=True, dxfattribs={"layer": "VERDE"}
    )
    msp.add_lwpolyline(
        [(40, 40), (50, 40), (50, 50), (40, 50)], close=True, dxfattribs={"layer": "EDIF"}
    )
    tree = doc.blocks.new("ARBOL")
    tree.add_lwpolyline([(-1, -1), (1, -1), (1, 1), (-1, 1)], close=True)
    for x in (20, 30, 60, 70):
        msp.add_blockref("ARBOL", (x, 20, 99), dxfattribs={"layer": "ARBOLES"})
    path = tmp_path / "slope.dxf"
    doc.saveas(path)
    return path


SLOPE_RULES = rules(
    {"match": "CURVAS", "kind": "terrain"},
    {"match": "VERDE", "kind": "vegetation", "height": 0.1, "base_from_terrain": True},
    {"match": "EDIF", "kind": "building", "height": 6, "base_from_terrain": True},
    {"match": "ARBOLES", "kind": "vegetation", "height": 4, "base_from_terrain": True},
)


def test_base_from_terrain_drapes_surfaces_and_seats_buildings(tmp_path: Path) -> None:
    result = build_scene(read_dxf(_slope(tmp_path)), SLOPE_RULES)
    assert not result.warnings

    (green,) = result.layers["VERDE"].objects
    v = green.mesh.vertices
    top = v[:, 2] > v[:, 1] / 2 + 0.05  # tapa superior: terreno + 0.1
    assert np.allclose(v[top, 2], v[top, 1] / 2 + 0.1)
    assert np.allclose(v[~top, 2], v[~top, 1] / 2)  # pie de las paredes sobre el terreno
    check = result.layers["VERDE"].stats.ground
    assert check is not None
    assert check.intersecting == check.partially_buried == check.misplaced == 0

    (building,) = result.layers["EDIF"].objects
    assert building.mesh.vertices[:, 2].min() == pytest.approx(20.0)  # y = 40 → cota mínima
    assert building.floor_elevation == pytest.approx(25.0)
    assert building.mesh.vertices[:, 2].max() == pytest.approx(31.0)
    check = result.layers["EDIF"].stats.ground
    assert check is not None
    assert check.intersecting == check.partially_buried == check.misplaced == 0

    trees = result.layers["ARBOLES"].objects
    assert len(trees) == 4
    for obj in trees:  # en llano (desnivel 1 m): base plana y siguen siendo instanciables
        assert obj.instance is not None
        assert obj.instance.matrix[14] == pytest.approx(obj.mesh.vertices[:, 2].min())
        assert obj.mesh.vertices[:, 2].min() == pytest.approx(9.5)


def test_base_from_terrain_without_terrain_warns(tmp_path: Path) -> None:
    config = rules({"match": "VERDE", "kind": "vegetation", "base_from_terrain": True})
    result = build_scene(read_dxf(_slope(tmp_path)), config)
    assert any("base_from_terrain" in w for w in result.warnings)
    assert result.layers["VERDE"].objects[0].mesh.vertices[:, 2].max() == 0


def test_base_from_terrain_excludes_base_from_elevation() -> None:
    with pytest.raises(Exception, match="excluyentes"):
        rules(
            {
                "match": "X",
                "kind": "building",
                "base_from_elevation": True,
                "base_from_terrain": True,
            }
        )


def _site(tmp_path: Path, terrain_z0: float, building_z: float = 0.0) -> Path:
    """Ladera (z = terrain_z0 + y/2) y un edificio y una mancha verde dibujados a building_z."""
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    for y in range(0, 101, 10):
        msp.add_lwpolyline(
            [(0, y), (100, y)], dxfattribs={"layer": "CURVAS", "elevation": terrain_z0 + y / 2}
        )
    for layer, (x0, y0, x1, y1) in {"EDIF": (40, 40, 50, 50), "VERDE": (10, 10, 30, 30)}.items():
        msp.add_lwpolyline(
            [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
            close=True,
            dxfattribs={"layer": layer, "elevation": building_z},
        )
    path = tmp_path / f"site_{terrain_z0}_{building_z}.dxf"
    doc.saveas(path)
    return path


def _site_rules(**extra: Any) -> Config:
    return rules(
        {"match": "CURVAS", "kind": "terrain"},
        {"match": "EDIF", "kind": "building", "height": 6, **extra},
        {"match": "VERDE", "kind": "vegetation", "height": 0.1, **extra},
    )


@pytest.mark.parametrize(
    ("terrain_z0", "floating", "buried"),
    [(-60.0, 1, 0), (20.0, 0, 1)],  # terreno bajo cota 0 → flotan; sobre ella → enterrados
)
def test_solids_off_the_terrain_are_warned(
    tmp_path: Path, terrain_z0: float, floating: int, buried: int
) -> None:
    result = build_scene(read_dxf(_site(tmp_path, terrain_z0)), _site_rules())
    (warning,) = [w for w in result.warnings if "no usan base_from_terrain" in w]
    assert "EDIF" in warning and "VERDE" in warning
    assert "base_from_terrain: true" in warning
    for layer in ("EDIF", "VERDE"):
        check = result.layers[layer].stats.ground
        assert check is not None
        assert (check.floating, check.buried, check.checked) == (floating, buried, 1)


def test_seated_solids_do_not_warn(tmp_path: Path) -> None:
    result = build_scene(read_dxf(_site(tmp_path, -60.0)), _site_rules(base_from_terrain=True))
    assert not result.warnings
    assert result.layers["EDIF"].stats.ground.misplaced == 0  # type: ignore[union-attr]


def test_base_from_elevation_without_z_is_warned(tmp_path: Path) -> None:
    config = rules(
        {"match": "CURVAS", "kind": "terrain"},
        {"match": "EDIF", "kind": "building", "height": 6, "base_from_elevation": True},
        {"match": "VERDE", "kind": "ignore"},
    )
    seated = build_scene(read_dxf(_site(tmp_path, -60.0, building_z=-40.0)), config)
    assert not seated.warnings  # y = 40..50: terreno de -40 a -35 m; base en -40, apoyada
    no_z = build_scene(read_dxf(_site(tmp_path, -60.0)), config)
    (warning,) = no_z.warnings
    assert "usa base_from_elevation" in warning and "1 flotan" in warning


def test_ground_check_goes_to_glb_and_report(tmp_path: Path) -> None:
    from extrude.convert import ConvertOptions, convert
    from extrude.gltf_export import decode_glb

    result = convert(_site(tmp_path, -60.0), ConvertOptions(_site_rules(), tmp_path / "out"))
    doc, _ = decode_glb((tmp_path / "out" / "model.glb").read_bytes())
    checks = {n["name"]: n["extras"].get("ground_check") for n in doc["nodes"] if "extras" in n}
    assert checks["EDIF"] == {
        "checked": 1,
        "floating": 1,
        "buried": 0,
        "intersecting": 0,
        "partially_buried": 0,
    }
    assert checks["CURVAS"] is None  # el terreno no se comprueba contra sí mismo
    assert result.report["layers"]["EDIF"]["ground_check"] == checks["EDIF"]
    report = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "no usan base_from_terrain" in report
    assert any("no usan base_from_terrain" in w for w in result.report["warnings"])


@pytest.mark.parametrize(
    ("base", "height", "category"),
    [
        (50, 10, "partially_buried"),
        (50, 60, "intersecting"),
        (102, 10, "floating"),
        (-20, 10, "buried"),
    ],
)
def test_ground_check_distinguishes_a_slope_from_full_flotation(
    tmp_path: Path, base: float, height: float, category: str
) -> None:
    doc = ezdxf.new()
    doc.units = 6
    msp = doc.modelspace()
    for x, y in ((0, 0), (10, 0), (0, 10), (10, 10)):
        msp.add_point((x, y, 10 * x), dxfattribs={"layer": "T"})
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "E"})
    path = tmp_path / "slope.dxf"
    doc.saveas(path)
    config = rules(
        {"match": "T", "kind": "terrain"},
        {"match": "E", "kind": "building", "base_elevation": base, "height": height},
    )
    result = build_scene(read_dxf(path), config)
    check = result.layers["E"].stats.ground
    assert check is not None
    assert check.as_dict()[category] == 1
    assert sum(v for k, v in check.as_dict().items() if k != "checked") == 1
    assert bool(result.warnings) == (category in ("floating", "buried"))


def test_ground_check_samples_a_ridge_inside_the_footprint(tmp_path: Path) -> None:
    doc = ezdxf.new()
    doc.units = 6
    msp = doc.modelspace()
    for x, y, z in [(0, 0, 0), (10, 0, 0), (0, 10, 0), (10, 10, 0), (5, 5, 100)]:
        msp.add_point((x, y, z), dxfattribs={"layer": "T"})
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "E"})
    path = tmp_path / "ridge.dxf"
    doc.saveas(path)
    config = rules(
        {"match": "T", "kind": "terrain"},
        {"match": "E", "kind": "building", "base_elevation": 50, "height": 10},
    )
    result = build_scene(read_dxf(path), config)
    check = result.layers["E"].stats.ground
    assert check is not None
    assert check.partially_buried == 1
    assert check.floating == 0
    assert not result.warnings
