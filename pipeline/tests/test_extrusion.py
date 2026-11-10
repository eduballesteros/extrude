from __future__ import annotations

import numpy as np
import pytest
import trimesh
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from extrude.extrusion import MeshData, extrude, triangle_areas


def as_trimesh(mesh: MeshData) -> trimesh.Trimesh:
    # merge_vertices une los vértices duplicados por el sombreado plano para validar el sólido
    m = trimesh.Trimesh(mesh.vertices, mesh.faces, process=False)
    m.merge_vertices()
    return m


PATIO = orient(
    Polygon([(0, 0), (30, 0), (30, 20), (0, 20)], [[(10, 5), (20, 5), (20, 15), (10, 15)]]), 1.0
)


def test_closed_solid_is_watertight_with_positive_volume() -> None:
    mesh = as_trimesh(extrude(PATIO, base=2.0, height=9.0, bottom=True))
    assert mesh.is_watertight
    assert mesh.is_winding_consistent
    assert mesh.volume == pytest.approx((600 - 100) * 9)  # >0 ⇒ normales hacia fuera
    assert mesh.bounds[:, 2] == pytest.approx([2.0, 11.0])


def test_wall_normals_point_away_from_solid() -> None:
    mesh = extrude(PATIO, base=0, height=3, bottom=False)
    tm = trimesh.Trimesh(mesh.vertices, mesh.faces, process=False)
    walls = np.abs(tm.face_normals[:, 2]) < 1e-9
    centers = tm.triangles_center[walls]
    normals = tm.face_normals[walls]
    probe = centers + normals * 0.01  # un paso hacia fuera de cada pared
    inside = np.array([PATIO.contains(_pt(p)) for p in probe])
    assert not inside.any()


def test_top_cap_faces_up_and_skips_patio() -> None:
    mesh = extrude(PATIO, base=0, height=3, bottom=False)
    tm = trimesh.Trimesh(mesh.vertices, mesh.faces, process=False)
    top = tm.face_normals[:, 2] > 0.5
    assert top.any()
    assert not (tm.face_normals[:, 2] < -0.5).any()  # sin tapa inferior
    patio = Polygon([(10, 5), (20, 5), (20, 15), (10, 15)])
    assert not any(patio.contains(_pt(c)) for c in tm.triangles_center[top])
    assert tm.area_faces[top].sum() == pytest.approx(500)


def test_no_degenerate_triangles() -> None:
    mesh = extrude(PATIO, base=0, height=3, bottom=True)
    assert triangle_areas(mesh.vertices, mesh.faces).min() > 1e-6


def test_zero_height_gives_flat_cap() -> None:
    mesh = extrude(PATIO, base=0.5, height=0, bottom=True)
    assert np.allclose(mesh.vertices[:, 2], 0.5)
    assert mesh.triangle_count > 0


def _pt(p: np.ndarray) -> object:
    from shapely.geometry import Point

    return Point(p[0], p[1])
