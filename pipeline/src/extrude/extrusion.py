"""Huella 2D → malla 3D (Z-up, metros, float64).

- Tapa superior triangulada con earcut (admite huecos), normal +Z.
- Paredes: un quad por arista con vértices propios (sombreado plano). Con exterior CCW y
  huecos CW, el lado derecho de cada arista es siempre "fuera del sólido".
- Tapa inferior opcional (normal -Z): solo si el edificio se ve desde abajo.
"""

from __future__ import annotations

from dataclasses import dataclass

import mapbox_earcut
import numpy as np
from numpy.typing import NDArray
from shapely.geometry import Polygon

MIN_TRIANGLE_AREA = 1e-10  # m²
MIN_EDGE_LENGTH = 1e-9  # m


@dataclass(frozen=True, slots=True)
class MeshData:
    vertices: NDArray[np.float64]  # (N, 3)
    faces: NDArray[np.int64]  # (M, 3), CCW visto desde fuera

    @property
    def triangle_count(self) -> int:
        return len(self.faces)

    @staticmethod
    def empty() -> MeshData:
        return MeshData(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64))


def concat(meshes: list[MeshData]) -> MeshData:
    if not meshes:
        return MeshData.empty()
    offsets = np.cumsum([0] + [len(m.vertices) for m in meshes[:-1]])
    vertices = np.vstack([m.vertices for m in meshes])
    faces = np.vstack([m.faces + off for m, off in zip(meshes, offsets, strict=True)])
    return MeshData(vertices, faces.astype(np.int64))


def triangle_areas(vertices: NDArray[np.float64], faces: NDArray[np.int64]) -> NDArray[np.float64]:
    a, b, c = (vertices[faces[:, i]] for i in range(3))
    u, v = b - a, c - a  # producto vectorial explícito: np.cross es lento en arrays pequeños
    cx = u[:, 1] * v[:, 2] - u[:, 2] * v[:, 1]
    cy = u[:, 2] * v[:, 0] - u[:, 0] * v[:, 2]
    cz = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
    result: NDArray[np.float64] = 0.5 * np.sqrt(cx * cx + cy * cy + cz * cz)
    return result


def drop_degenerate(mesh: MeshData) -> tuple[MeshData, int]:
    if mesh.triangle_count == 0:
        return mesh, 0
    keep = triangle_areas(mesh.vertices, mesh.faces) > MIN_TRIANGLE_AREA
    return MeshData(mesh.vertices, mesh.faces[keep]), int((~keep).sum())


def extrude(polygon: Polygon, base: float, height: float, *, bottom: bool) -> MeshData:
    """Extruye `polygon` (exterior CCW, huecos CW) de `base` a `base + height`."""
    rings = [_ring(polygon.exterior.coords)] + [_ring(r.coords) for r in polygon.interiors]
    top = _cap(rings, base + height, up=True)
    if height <= 0:
        return top
    parts = [top, _walls(rings, base, base + height)]
    if bottom:
        parts.append(_cap(rings, base, up=False))
    return concat(parts)


def _ring(coords: object) -> NDArray[np.float64]:
    ring = np.asarray(coords, dtype=np.float64)[:, :2]
    if len(ring) > 1 and (ring[0] == ring[-1]).all():  # shapely cierra los anillos exactamente
        ring = ring[:-1]
    keep = np.linalg.norm(ring - np.roll(ring, 1, axis=0), axis=1) > MIN_EDGE_LENGTH
    return ring[keep]


def _cap(rings: list[NDArray[np.float64]], z: float, *, up: bool) -> MeshData:
    flat = np.vstack(rings)
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    indices = mapbox_earcut.triangulate_float64(flat, ends).astype(np.int64).reshape(-1, 3)
    a, b, c = (flat[indices[:, i]] for i in range(3))
    signed = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    flip = signed < 0 if up else signed > 0
    indices[flip] = indices[flip][:, ::-1]
    vertices = np.column_stack([flat, np.full(len(flat), z)])
    return MeshData(vertices, indices)


def _walls(rings: list[NDArray[np.float64]], z0: float, z1: float) -> MeshData:
    quads = []
    for ring in rings:
        a = ring
        b = np.roll(ring, -1, axis=0)
        n = len(ring)
        verts = np.empty((n, 4, 3))
        verts[:, 0, :2], verts[:, 0, 2] = a, z0
        verts[:, 1, :2], verts[:, 1, 2] = b, z0
        verts[:, 2, :2], verts[:, 2, 2] = b, z1
        verts[:, 3, :2], verts[:, 3, 2] = a, z1
        base = np.arange(n)[:, None] * 4
        faces = np.concatenate([base + np.array([0, 1, 2]), base + np.array([0, 2, 3])])
        quads.append(MeshData(verts.reshape(-1, 3), faces.astype(np.int64)))
    return concat(quads)
