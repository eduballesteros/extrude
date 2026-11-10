"""Geometría 3D ya existente en el DXF (3DFACE, SOLID, MESH, polyface) y terreno TIN."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.spatial import Delaunay, QhullError

from extrude.extrusion import MeshData, drop_degenerate
from extrude.model import BBox, MeshFeature

MIN_TIN_POINTS = 3


def mesh_from_feature(feature: MeshFeature, scale: float) -> tuple[MeshData, int]:
    """Triangula en abanico cada cara (respeta el orden de vértices de origen)."""
    triangles = [
        (face[0], face[i], face[i + 1]) for face in feature.faces for i in range(1, len(face) - 1)
    ]
    faces = np.array(triangles, dtype=np.int64).reshape(-1, 3)
    return drop_degenerate(MeshData(feature.vertices * scale, faces))


def tin(points: NDArray[np.float64], tolerance: float) -> MeshData | None:
    """Triangulación de Delaunay en XY; normales hacia +Z. None si no hay datos suficientes."""
    keys = np.round(points[:, :2] / tolerance).astype(np.int64)
    _, unique_idx = np.unique(keys, axis=0, return_index=True)
    pts = points[np.sort(unique_idx)]
    if len(pts) < MIN_TIN_POINTS:
        return None
    try:
        faces = Delaunay(pts[:, :2]).simplices.astype(np.int64)
    except QhullError:  # puntos colineales
        return None
    a, b, c = (pts[faces[:, i], :2] for i in range(3))
    signed = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    faces[signed < 0] = faces[signed < 0][:, ::-1]
    return MeshData(pts, faces)


def base_plane(bbox: BBox, scale: float, z: float) -> MeshData:
    (x0, y0, _), (x1, y1, _) = bbox.min, bbox.max
    corners = np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], dtype=np.float64) * scale
    vertices = np.column_stack([corners, np.full(4, z)])
    return MeshData(vertices, np.array([(0, 1, 2), (0, 2, 3)], dtype=np.int64))
