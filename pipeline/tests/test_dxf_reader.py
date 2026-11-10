from __future__ import annotations

from pathlib import Path

import ezdxf
import numpy as np
import pytest
import sample_dxf as s

from extrude.dxf_reader import read_dxf
from extrude.errors import InputFileError
from extrude.model import (
    HatchFeature,
    LengthUnit,
    MeshFeature,
    PolylineFeature,
    ReadResult,
    TextFeature,
)


def polylines(result: ReadResult, layer: str) -> list[PolylineFeature]:
    return [f for f in result.features if isinstance(f, PolylineFeature) and f.layer == layer]


def meshes(result: ReadResult, layer: str) -> list[MeshFeature]:
    return [f for f in result.features if isinstance(f, MeshFeature) and f.layer == layer]


def shoelace(points: np.ndarray) -> float:
    x, y = points[:, 0], points[:, 1]
    return float(0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


class TestBlock2D:
    @pytest.fixture(scope="class")
    @classmethod
    def result(cls, samples_dir: Path) -> ReadResult:
        return read_dxf(samples_dir / "block_2d.dxf")

    def test_units_and_version(self, result: ReadResult) -> None:
        assert result.units is LengthUnit.M
        assert not result.units_assumed
        assert result.dxf_version == "R2018"

    def test_buildings_are_closed_polylines(self, result: ReadResult) -> None:
        buildings = polylines(result, "EDIFICIOS")
        expected = len(s.BUILDINGS) + sum(len(b.holes) for b in s.BUILDINGS)
        assert len(buildings) == expected
        assert all(b.closed for b in buildings)
        assert shoelace(buildings[0].points) == pytest.approx(20 * 12)

    def test_bulge_is_flattened_within_tolerance(self, result: ReadResult) -> None:
        curved = polylines(result, "EDIFICIOS")[-1]
        assert len(curved.points) > 4
        top = curved.points[(curved.points[:, 0] > 35) & (curved.points[:, 0] < 50)]
        sagitta = 0.5 * 15 / 2
        assert np.abs(top[:, 1] - 35).max() == pytest.approx(sagitta, abs=0.01)

    def test_floor_labels_with_positions(self, result: ReadResult) -> None:
        labels = {
            f.text: tuple(f.position[:2])
            for f in result.features
            if isinstance(f, TextFeature) and f.layer == "EDIFICIOS_TEXTO"
        }
        assert labels == {f"{b.floors}P": b.label_at for b in s.BUILDINGS}

    def test_bbox(self, result: ReadResult) -> None:
        assert result.bbox is not None
        assert result.bbox.min[:2] == (-5, -20)
        assert result.bbox.max[:2] == (70, 40)


def test_utm_coordinates_keep_float64_precision(samples_dir: Path) -> None:
    local = read_dxf(samples_dir / "block_2d.dxf")
    utm = read_dxf(samples_dir / "block_2d_utm.dxf")
    offset = np.array([*s.UTM_OFFSET, 0.0])
    for a, b in zip(polylines(local, "EDIFICIOS"), polylines(utm, "EDIFICIOS"), strict=True):
        assert b.points.dtype == np.float64
        np.testing.assert_allclose(b.points - offset, a.points, atol=1e-9)


class TestNestedBlocks:
    @pytest.fixture(scope="class")
    @classmethod
    def result(cls, samples_dir: Path) -> ReadResult:
        return read_dxf(samples_dir / "nested_blocks.dxf")

    def tree_circles(self, result: ReadResult) -> list[PolylineFeature]:
        return [f for f in polylines(result, "VEGETACION") if f.source.dxftype == "CIRCLE"]

    def test_all_tree_instances_expanded_on_parent_layer(self, result: ReadResult) -> None:
        assert len(self.tree_circles(result)) == s.TOTAL_TREES
        assert result.read[("0", "CIRCLE")] == 0

    def test_nested_scale_and_block_path(self, result: ReadResult) -> None:
        radii = set()
        for circle in self.tree_circles(result):
            center = circle.points.mean(axis=0)
            radii.add(round(float(np.linalg.norm(circle.points[0] - center)), 2))
        assert radii == {s.TREE_RADIUS, s.TREE_RADIUS * 2}
        paths = {c.source.block_path for c in self.tree_circles(result)}
        assert paths == {("ARBOL",), ("GRUPO_ARBOLES", "ARBOL")}

    def test_inverted_ocs_mirrors_insert_point(self, result: ReadResult) -> None:
        centers = [c.points.mean(axis=0) for c in self.tree_circles(result)]
        assert any(np.allclose(c[:2], (-s.MIRRORED_TREE_OCS_X, 0), atol=1e-6) for c in centers)

    def test_entities_on_own_layer_keep_it(self, result: ReadResult) -> None:
        assert len(polylines(result, "MOBILIARIO")) == s.GROUP_INSERTS

    def test_attributes_propagate_to_block_geometry(self, result: ReadResult) -> None:
        values = [dict(f.source.attributes)["PLANTAS"] for f in polylines(result, "EDIFICIOS")]
        assert sorted(int(v) for v in values) == list(s.TYPICAL_BUILDING_FLOORS)
        tags = [f.tag for f in result.features if isinstance(f, TextFeature)]
        assert tags == ["PLANTAS"] * len(s.TYPICAL_BUILDING_FLOORS)


class TestFaces3D:
    @pytest.fixture(scope="class")
    @classmethod
    def result(cls, samples_dir: Path) -> ReadResult:
        return read_dxf(samples_dir / "faces_3d.dxf")

    def test_millimetres_detected(self, result: ReadResult) -> None:
        assert result.units is LengthUnit.MM

    def test_mesh_entities(self, result: ReadResult) -> None:
        assert len(meshes(result, "CUBO")) == 6
        (mesh,) = meshes(result, "MALLA")
        assert len(mesh.faces) == 5
        (polyface,) = meshes(result, "POLYFACE")
        assert polyface.faces == ((0, 1, 2),)

    def test_solid_vertices_in_drawing_order(self, result: ReadResult) -> None:
        (solid,) = meshes(result, "SOLID2D")
        assert shoelace(solid.vertices) == pytest.approx(5_000 * 3_000)

    def test_acis_is_counted_as_unsupported(self, result: ReadResult) -> None:
        assert result.unsupported == {("SOLIDOS", "3DSOLID"): 2, ("SOLIDOS", "BODY"): 1}


class TestDirty:
    @pytest.fixture(scope="class")
    @classmethod
    def result(cls, samples_dir: Path) -> ReadResult:
        return read_dxf(samples_dir / "dirty.dxf")

    def test_open_and_loose_geometry_is_kept_for_topology(self, result: ReadResult) -> None:
        edif = polylines(result, "EDIFICIOS")
        open_ones = [f for f in edif if not f.closed]
        assert len(open_ones) == 1 + 4 + 4 + 1  # abierta + rect. + lazo LINE/ARC + colgante
        assert len(edif) - len(open_ones) == 4  # duplicados, pajarita, colineal

    def test_hatch_has_outer_and_hole(self, result: ReadResult) -> None:
        (hatch,) = [f for f in result.features if isinstance(f, HatchFeature)]
        assert sorted(shoelace(loop) for loop in hatch.loops) == [100, 900]

    def test_spline_is_closed(self, result: ReadResult) -> None:
        (spline,) = polylines(result, "ZONAS_VERDES")
        assert spline.closed
        assert len(spline.points) > 10

    def test_dimension_is_reported_as_skipped(self, result: ReadResult) -> None:
        assert sum(n for (_, t), n in result.skipped.items() if t == "DIMENSION") == 1


def test_unitless_drawing_assumes_metres_with_warning(tmp_path: Path) -> None:
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 0
    doc.modelspace().add_line((0, 0), (1, 0))
    path = tmp_path / "unitless.dxf"
    doc.saveas(path)
    result = read_dxf(path)
    assert result.units is LengthUnit.M
    assert result.units_assumed
    assert any("--units" in w for w in result.warnings)
    assert read_dxf(path, units_override=LengthUnit.CM).units is LengthUnit.CM


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(InputFileError, match="No existe"):
        read_dxf(tmp_path / "nope.dxf")


def test_garbage_file(tmp_path: Path) -> None:
    path = tmp_path / "garbage.dxf"
    path.write_bytes(b"\x00\x01 esto no es un dxf")
    with pytest.raises(InputFileError, match="no es un DXF"):
        read_dxf(path)
