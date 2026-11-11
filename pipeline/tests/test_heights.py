"""Textos de altura justo fuera del contorno (rescate con text_tolerance)."""

from __future__ import annotations

from pathlib import Path

import ezdxf
import numpy as np
import pytest
from shapely.geometry import Polygon, box
from shapely.geometry.polygon import orient

from extrude.build import build_scene
from extrude.config import LayerRule, parse_config
from extrude.dxf_reader import read_dxf
from extrude.heights import HeightOrigin, Neighbors, TextIndex, resolve_height
from extrude.model import Provenance, TextFeature
from extrude.topology import Footprint

SRC = Provenance("LWPOLYLINE", None)


def footprint(polygon: Polygon) -> Footprint:
    return Footprint(orient(polygon, sign=1.0), 0.0, (SRC,))


def text(value: str, x: float, y: float, *, tag: str | None = None) -> TextFeature:
    return TextFeature("TEXTOS", value, np.array([x, y, 0.0]), Provenance("TEXT", None), tag)


def rule(**extra: object) -> LayerRule:
    raw = {"match": "E", "kind": "building", "height_from": "text", "default_height": 99, **extra}
    return LayerRule.model_validate(raw)


A = footprint(box(0, 0, 10, 10))


def test_text_10_cm_outside_is_rescued() -> None:
    index = TextIndex([text("3P", 10.1, 5)], scale=1)
    result = resolve_height(A, rule(), index)
    assert result.height == pytest.approx(9.0)
    assert result.origin is HeightOrigin.TEXT
    assert result.nearby


@pytest.mark.parametrize(("x", "y"), [(0, 5), (0, 0), (10, 5)])
def test_text_on_exterior_facade_is_accepted_even_without_rescue(x: float, y: float) -> None:
    result = resolve_height(A, rule(text_tolerance=0), TextIndex([text("3P", x, y)], 1))
    assert result.height == pytest.approx(9)
    assert not result.nearby


def test_shared_facade_is_ambiguous_and_patio_boundary_is_excluded() -> None:
    b = footprint(box(10, 0, 20, 10))
    neighbors = Neighbors([A, b])
    index = TextIndex([text("3P", 10, 5)], 1)
    for fp in (A, b):
        assert resolve_height(fp, rule(), index, neighbors).origin is HeightOrigin.DEFAULT
    patio = footprint(Polygon(box(0, 0, 20, 20).exterior, [box(8, 8, 12, 12).exterior.coords]))
    index = TextIndex([text("3P", 8, 10)], 1)
    assert resolve_height(patio, rule(), index).origin is HeightOrigin.DEFAULT


def test_rescue_respects_tolerance_and_can_be_disabled() -> None:
    far = TextIndex([text("3P", 11.5, 5)], scale=1)
    assert resolve_height(A, rule(), far).origin is HeightOrigin.DEFAULT  # 1,5 m > 1 m
    assert resolve_height(A, rule(text_tolerance=2), far).height == pytest.approx(9.0)
    near = TextIndex([text("3P", 10.1, 5)], scale=1)
    assert resolve_height(A, rule(text_tolerance=0), near).origin is HeightOrigin.DEFAULT


def test_text_inside_wins_over_text_outside() -> None:
    index = TextIndex([text("5P", 10.02, 5), text("2P", 5, 5)], scale=1)
    result = resolve_height(A, rule(), index)
    assert result.height == pytest.approx(6.0)
    assert not result.nearby


def test_rescue_when_inside_text_does_not_match_regex() -> None:
    index = TextIndex([text("Calle Mayor", 5, 5), text("4P", -0.2, 5)], scale=1)
    assert resolve_height(A, rule(), index).height == pytest.approx(12.0)


def test_outside_candidates_sorted_by_distance_to_edge() -> None:
    index = TextIndex([text("6P", 10.8, 5), text("2P", 5, 10.05)], scale=1)
    result = resolve_height(A, rule(), index)
    assert result.height == pytest.approx(6.0)  # 2P está a 5 cm; 6P a 80 cm
    assert result.ambiguous


def test_neighbour_text_is_not_stolen_across_party_wall() -> None:
    """Medianera: el rótulo dentro del vecino (a 5 cm de A) es del vecino."""
    b = footprint(box(10, 0, 20, 10))
    neighbors = Neighbors([A, b])
    index = TextIndex([text("4P", 10.05, 5)], scale=1)
    assert resolve_height(b, rule(), index, neighbors).height == pytest.approx(12.0)
    assert resolve_height(A, rule(), index, neighbors).origin is HeightOrigin.DEFAULT


def test_text_in_a_gap_goes_to_the_nearest_footprint_only() -> None:
    b = footprint(box(10.5, 0, 20, 10))  # calle de 50 cm entre A y B
    neighbors = Neighbors([A, b])
    closer_to_b = TextIndex([text("4P", 10.4, 5)], scale=1)
    assert resolve_height(b, rule(), closer_to_b, neighbors).nearby
    assert resolve_height(A, rule(), closer_to_b, neighbors).origin is HeightOrigin.DEFAULT
    middle = TextIndex([text("4P", 10.25, 5)], scale=1)  # equidistante: de ninguno
    for fp in (A, b):
        assert resolve_height(fp, rule(), middle, neighbors).origin is HeightOrigin.DEFAULT


def test_text_in_a_patio_is_not_rescued() -> None:
    with_patio = footprint(Polygon(box(0, 0, 20, 20).exterior, [box(8, 8, 12, 12).exterior.coords]))
    index = TextIndex([text("7P", 10, 11.9)], scale=1)  # en el patio, a 10 cm del borde
    assert resolve_height(with_patio, rule(), index).origin is HeightOrigin.DEFAULT


def test_attribute_label_block_just_outside_is_rescued() -> None:
    index = TextIndex([text("5", 10.1, 5, tag="PLANTAS")], scale=1)
    result = resolve_height(A, rule(height_from="attribute", attribute="plantas"), index)
    assert result.height == pytest.approx(15.0)
    assert result.nearby


def test_rescued_heights_are_counted_and_reported(tmp_path: Path) -> None:
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "E"})
    msp.add_text("3P", dxfattribs={"layer": "TEXTOS", "insert": (10.1, 5)})
    path = tmp_path / "fachada.dxf"
    doc.saveas(path)
    config = parse_config(
        {"layers": [rule().model_dump(mode="json"), {"match": "*", "kind": "ignore"}]}
    )
    result = build_scene(read_dxf(path), config)
    (building,) = result.layers["E"].objects
    assert building.height == pytest.approx(9.0)
    assert result.layers["E"].stats.nearby_text_heights == 1
