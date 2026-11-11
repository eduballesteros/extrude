from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest

from extrude.dxf_reader import read_dxf
from extrude.model import HatchFeature, PolylineFeature, Provenance
from extrude.topology import build_footprints

SRC = Provenance("LWPOLYLINE", None)


def poly(points: list[tuple[float, float]], *, closed: bool = True) -> PolylineFeature:
    arr = np.array([(x, y, 0.0) for x, y in points])
    return PolylineFeature("L", arr, closed, SRC)


SQUARE_10 = [(0, 0), (10, 0), (10, 10), (0, 10)]
SQUARE_IN = [(3, 3), (6, 3), (6, 6), (3, 6)]
SQUARE_ISLAND = [(4, 4), (5, 4), (5, 5), (4, 5)]


def test_nested_rings_alternate_solid_hole_solid() -> None:
    footprints, stats = build_footprints(
        [poly(SQUARE_10), poly(SQUARE_IN), poly(SQUARE_ISLAND)], [], scale=1, tolerance=0.01
    )
    assert stats.holes == 1
    areas = sorted(f.polygon.area for f in footprints)
    assert areas == pytest.approx([1, 100 - 9])


def test_holes_can_be_disabled() -> None:
    footprints, stats = build_footprints(
        [poly(SQUARE_10), poly(SQUARE_IN)], [], scale=1, tolerance=0.01, detect_holes=False
    )
    assert stats.holes == 0
    assert len(footprints) == 2


def test_orientation_exterior_ccw_holes_cw() -> None:
    clockwise = list(reversed(SQUARE_10))
    (fp,), _ = build_footprints([poly(clockwise), poly(SQUARE_IN)], [], scale=1, tolerance=0.01)
    assert fp.polygon.exterior.is_ccw
    assert not fp.polygon.interiors[0].is_ccw


def test_scale_is_applied() -> None:
    (fp,), _ = build_footprints([poly(SQUARE_10)], [], scale=0.001, tolerance=1e-6)
    assert fp.polygon.area == pytest.approx(1e-4)


def test_segments_snap_within_tolerance() -> None:
    gap = 0.005
    lines = [
        poly([(0, 0), (10, 0)], closed=False),
        poly([(10, gap), (10, 10)], closed=False),
        poly([(10, 10), (0, 10)], closed=False),
        poly([(0, 10), (0, gap)], closed=False),
    ]
    footprints, stats = build_footprints(lines, [], scale=1, tolerance=0.01)
    assert stats.loops_from_segments == 1
    assert footprints[0].polygon.area == pytest.approx(100, rel=1e-3)


def test_segments_beyond_tolerance_stay_open() -> None:
    lines = [
        poly([(0, 0), (10, 0), (10, 10)], closed=False),
        poly([(9, 10), (0, 0.5)], closed=False),
    ]
    footprints, stats = build_footprints(lines, [], scale=1, tolerance=0.01)
    assert footprints == []
    assert stats.dangling_segments >= 1


def test_hatch_loops_use_even_odd() -> None:
    loops = tuple(np.array([(x, y, 0.0) for x, y in sq]) for sq in (SQUARE_10, SQUARE_IN))
    (fp,), stats = build_footprints(
        [], [HatchFeature("L", loops, SRC)], scale=1, tolerance=0.01, detect_holes=False
    )
    assert fp.polygon.area == pytest.approx(91)
    assert stats.holes == 1


def test_dirty_fixture_repairs_are_counted(samples_dir: Path) -> None:
    read = read_dxf(samples_dir / "dirty.dxf")
    edif = [f for f in read.features if isinstance(f, PolylineFeature) and f.layer == "EDIFICIOS"]
    footprints, stats = build_footprints(edif, [], scale=1, tolerance=0.01)
    assert stats.closed_by_tolerance == 1
    assert stats.loops_from_segments == 2  # rectángulo de LINE + lazo LINE/ARC
    assert stats.dangling_segments == 1
    assert stats.duplicates_removed == 1
    assert stats.repaired == 1  # pajarita → 2 triángulos
    assert stats.degenerate_dropped == 1  # colineal
    assert len(footprints) == 6
    assert all(f.polygon.is_valid for f in footprints)
    rounded = next(f for f in footprints if f.polygon.bounds[0] == pytest.approx(40))
    assert rounded.polygon.area == pytest.approx(10 * 5 + np.pi * 25 / 2, rel=1e-3)


def _segments_at(square: list[tuple[float, float]], z: float) -> list[PolylineFeature]:
    """Cuadrado dibujado como cuatro segmentos abiertos a cota z."""
    corners = [*square, square[0]]
    return [
        PolylineFeature("L", np.array([(*a, z), (*b, z)], dtype=np.float64), False, SRC)
        for a, b in pairwise(corners)
    ]


def test_segment_rings_keep_their_own_elevation() -> None:
    low = _segments_at([(0, 0), (10, 0), (10, 10), (0, 10)], 10.0)
    high = _segments_at([(50, 0), (60, 0), (60, 10), (50, 10)], 50.0)
    degenerate = PolylineFeature("L", np.array([(30, 30, 99.0)]), False, SRC)  # 1 punto
    footprints, stats = build_footprints([degenerate, *low, *high], [], scale=1, tolerance=0.01)
    assert stats.loops_from_segments == 2
    by_x = sorted(footprints, key=lambda f: f.polygon.bounds[0])
    assert [f.elevation for f in by_x] == [10.0, 50.0]
    assert all(len(f.sources) == 4 for f in by_x)


@pytest.mark.parametrize("scale", [1, 0.001])
def test_level_topology_keeps_same_level_duplicates_and_patios(scale: float) -> None:
    lines = []
    for z in (0, 20):
        for shape in (SQUARE_10, list(reversed(SQUARE_10)), SQUARE_IN):
            lines.append(
                PolylineFeature("L", np.array([(*p, z) for p in shape]) / scale, True, SRC)
            )
    footprints, stats = build_footprints(
        lines,
        [],
        scale=scale,
        tolerance=0.01,
        separate_elevations=True,
    )
    assert sorted(f.elevation for f in footprints) == [0, 20]
    assert [f.polygon.area for f in footprints] == [91, 91]
    assert stats.duplicates_removed == 2
    assert stats.holes == 2


def test_contained_outline_at_another_level_is_not_a_patio() -> None:
    outer = poly(SQUARE_10)
    upper = PolylineFeature("L", np.array([(*p, 20.0) for p in SQUARE_IN]), True, SRC)
    footprints, stats = build_footprints(
        [outer, upper],
        [],
        scale=1,
        tolerance=0.01,
        separate_elevations=True,
    )
    assert len(footprints) == 2
    assert sorted(f.polygon.area for f in footprints) == [9, 100]
    assert stats.holes == 0


def test_two_dimensional_rules_keep_xy_deduplication() -> None:
    lower = poly(SQUARE_10)
    upper = PolylineFeature("L", lower.points + np.array([0, 0, 20]), True, SRC)
    footprints, stats = build_footprints([lower, upper], [], scale=1, tolerance=0.01)
    assert len(footprints) == 1
    assert stats.duplicates_removed == 1


def test_level_deduplication_uses_metric_tolerance() -> None:
    lower = poly(SQUARE_10)
    near = PolylineFeature("L", lower.points + np.array([0, 0, 0.001]), True, SRC)
    upper = PolylineFeature("L", lower.points + np.array([0, 0, 0.02]), True, SRC)
    footprints, stats = build_footprints(
        [lower, near, upper],
        [],
        scale=1,
        tolerance=0.01,
        separate_elevations=True,
    )
    assert len(footprints) == 2
    assert stats.duplicates_removed == 1
