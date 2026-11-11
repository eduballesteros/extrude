"""Extremos del terreno bajo una huella (range_under) con valles y crestas que cruzan su borde."""

from __future__ import annotations

from pathlib import Path

import ezdxf
import numpy as np
import pytest
from shapely.geometry import Polygon, box

from extrude.build import build_scene
from extrude.config import parse_config
from extrude.dxf_reader import read_dxf
from extrude.terrain import TerrainSurface


def valley(depth_sign: float = 1.0) -> TerrainSurface:
    """Valle en x = 5 (z = 0) con bordes x = 0 y 10 a z = 10; con signo negativo, cresta."""
    points = [[x, y, 5 + depth_sign * (abs(x - 5) * 2 - 5)] for x in (0, 5, 10) for y in (-50, 50)]
    return TerrainSurface(np.array(points, dtype=float))


def test_valley_crossing_the_outline_sets_the_minimum() -> None:
    # Ni las esquinas (x = 1 y 9, z = 8) ni ningún vértice del TIN interior ven el fondo del valle
    assert valley().range_under(box(1, -1, 9, 1)) == pytest.approx((0.0, 8.0))


def test_ridge_crossing_the_outline_sets_the_maximum() -> None:
    assert valley(-1.0).range_under(box(1, -1, 9, 1)) == pytest.approx((2.0, 10.0))


def test_outline_without_crossings_still_uses_corners_and_inner_vertices() -> None:
    assert valley().range_under(box(6, -1, 9, 1)) == pytest.approx((2.0, 8.0))
    assert valley().range_under(box(4, -60, 6, 60)) == pytest.approx((0.0, 2.0))


def test_patio_edges_are_sampled_too() -> None:
    """El valle solo toca el borde de un patio: también es superficie bajo el edificio."""
    outer = box(5.5, -3, 9, 3)
    patio = Polygon(outer.exterior, [box(5.6, -1, 8, 1).exterior.coords])
    low, _ = valley().range_under(patio)
    assert low == pytest.approx(1.0)  # x = 5.5 en el contorno exterior


def test_building_over_a_valley_sits_at_the_lowest_ground(tmp_path: Path) -> None:
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    for x, z in ((0, 10), (5, 0), (10, 10)):
        msp.add_lwpolyline([(x, -50), (x, 50)], dxfattribs={"layer": "CURVAS", "elevation": z})
    msp.add_lwpolyline([(1, -1), (9, -1), (9, 1), (1, 1)], close=True, dxfattribs={"layer": "E"})
    path = tmp_path / "valle.dxf"
    doc.saveas(path)
    config = parse_config(
        {
            "layers": [
                {"match": "CURVAS", "kind": "terrain"},
                {
                    "match": "E",
                    "kind": "building",
                    "height": 6,
                    "base_from_terrain": True,
                    "terrain_support": "minimum",
                },
                {"match": "*", "kind": "ignore"},
            ]
        }
    )
    (building,) = build_scene(read_dxf(path), config).layers["E"].objects
    assert building.mesh.vertices[:, 2].min() == pytest.approx(0.0)  # no 8 m en el aire
    assert building.mesh.vertices[:, 2].max() == pytest.approx(6.0)
