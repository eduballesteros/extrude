"""Huella 2D → malla 3D (Z-up, metros, float64).

- Tapa superior triangulada con earcut (admite huecos), normal +Z.
- Paredes: un quad por arista con vértices propios (sombreado plano). Con exterior CCW y
  huecos CW, el lado derecho de cada arista es siempre "fuera del sólido".
- Tapa inferior opcional (normal -Z): solo si el edificio se ve desde abajo.
- UV de pared en metros (RG-03): u = distancia recorrida sobre el contorno (continua en las
  esquinas, creciente de izquierda a derecha visto desde fuera), v = cota absoluta, así las hiladas
  quedan horizontales y alineadas entre paños. Las tapas no llevan UV propias (NaN): al exportar
  se proyectan en planta.
- Cada triángulo lleva su superficie semántica (`surfaces.Surface`): cubierta, fachada exterior,
  fachada de patio, tapa inferior. Una huella sin altura es suelo (`GROUND`).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import mapbox_earcut
import numpy as np
from numpy.typing import NDArray
from shapely.geometry import Polygon

from extrude.surfaces import Surface

MIN_TRIANGLE_AREA = 1e-10  # m²
MIN_EDGE_LENGTH = 1e-9  # m


@dataclass(frozen=True, slots=True)
class MeshData:
    vertices: NDArray[np.float64]  # (N, 3)
    faces: NDArray[np.int64]  # (M, 3), CCW visto desde fuera
    # (M,) uint8 con un `Surface` por triángulo; None = todo GENERIC
    surfaces: NDArray[np.uint8] | None = None
    # (N, 2) UV en metros por vértice; filas NaN (o None) = proyección en planta al exportar
    uvs: NDArray[np.float64] | None = None

    @property
    def triangle_count(self) -> int:
        return len(self.faces)

    @property
    def face_surfaces(self) -> NDArray[np.uint8]:
        if self.surfaces is not None:
            return self.surfaces
        return np.full(len(self.faces), Surface.GENERIC, dtype=np.uint8)

    @property
    def vertex_uvs(self) -> NDArray[np.float64]:
        if self.uvs is not None:
            return self.uvs
        return np.full((len(self.vertices), 2), np.nan)

    @staticmethod
    def empty() -> MeshData:
        return MeshData(
            np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), np.zeros(0, dtype=np.uint8)
        )


def labeled(mesh: MeshData, surface: Surface) -> MeshData:
    """La misma malla con todos sus triángulos en `surface`."""
    return replace(mesh, surfaces=np.full(len(mesh.faces), surface, dtype=np.uint8))


def as_ground(mesh: MeshData) -> MeshData:
    """Viales, agua, vegetación: su cubierta y sus cantos son una sola superficie de suelo
    (la tapa inferior se conserva aparte)."""
    surfaces = mesh.face_surfaces.copy()
    surfaces[surfaces != Surface.UNDERSIDE] = Surface.GROUND
    return replace(mesh, surfaces=surfaces)


def concat(meshes: list[MeshData]) -> MeshData:
    if not meshes:
        return MeshData.empty()
    offsets = np.cumsum([0] + [len(m.vertices) for m in meshes[:-1]])
    vertices = np.vstack([m.vertices for m in meshes])
    faces = np.vstack([m.faces + off for m, off in zip(meshes, offsets, strict=True)])
    surfaces = (
        np.concatenate([m.face_surfaces for m in meshes])
        if any(m.surfaces is not None for m in meshes)
        else None
    )
    uvs = (
        np.vstack([m.vertex_uvs for m in meshes])
        if any(m.uvs is not None for m in meshes)
        else None
    )
    return MeshData(vertices, faces.astype(np.int64), surfaces, uvs)


def box_corners(vertices: NDArray[np.float64]) -> NDArray[np.float64]:
    """Ocho esquinas de la caja: deben transformarse todas si hay rotación."""
    lo, hi = vertices.min(axis=0), vertices.max(axis=0)
    return np.array(
        [[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    )


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
    surfaces = mesh.surfaces[keep] if mesh.surfaces is not None else None
    return replace(mesh, faces=mesh.faces[keep], surfaces=surfaces), int((~keep).sum())


def extrude(polygon: Polygon, base: float, height: float, *, bottom: bool) -> MeshData:
    """Extruye `polygon` (exterior CCW, huecos CW) de `base` a `base + height`."""
    rings = [_ring(polygon.exterior.coords)] + [_ring(r.coords) for r in polygon.interiors]
    top = _cap(rings, base + height, up=True)
    if height <= 0:
        return labeled(top, Surface.GROUND)
    parts = [labeled(top, Surface.ROOF), _walls(rings, base, base + height)]
    if bottom:
        parts.append(labeled(_cap(rings, base, up=False), Surface.UNDERSIDE))
    return concat(parts)


def ring_surface(index: int) -> Surface:
    """Anillo 0 = contorno exterior; el resto son huecos interiores (patios)."""
    return Surface.FACADE_EXTERIOR if index == 0 else Surface.FACADE_COURTYARD


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
    for index, ring in enumerate(rings):
        n = len(ring)
        quads.append(wall_quads(ring, np.full(n, z0), np.full(n, z1), ring_surface(index)))
    return concat(quads)


def wall_quads(
    ring: NDArray[np.float64],
    bottom: NDArray[np.float64],
    top: NDArray[np.float64],
    surface: Surface,
) -> MeshData:
    """Un quad por arista del anillo cerrado `ring` (sin repetir el cierre), con cotas propias
    en cada vértice, vértices propios y UV de pared en metros."""
    n = len(ring)
    if n < 2:
        return MeshData.empty()
    nxt = np.roll(np.arange(n), -1)
    verts = np.empty((n, 4, 3))
    verts[:, 0, :2], verts[:, 0, 2] = ring, bottom
    verts[:, 1, :2], verts[:, 1, 2] = ring[nxt], bottom[nxt]
    verts[:, 2, :2], verts[:, 2, 2] = ring[nxt], top[nxt]
    verts[:, 3, :2], verts[:, 3, 2] = ring, top
    lengths = np.linalg.norm(ring[nxt] - ring, axis=1)
    start = np.concatenate([[0.0], np.cumsum(lengths)[:-1]])
    uvs = np.empty((n, 4, 2))
    uvs[:, 0, 0] = uvs[:, 3, 0] = start
    uvs[:, 1, 0] = uvs[:, 2, 0] = start + lengths
    uvs[:, :, 1] = verts[:, :, 2]
    base = np.arange(n)[:, None] * 4
    faces = np.concatenate([base + np.array([0, 1, 2]), base + np.array([0, 2, 3])])
    surfaces = np.full(len(faces), surface, dtype=np.uint8)
    return MeshData(verts.reshape(-1, 3), faces.astype(np.int64), surfaces, uvs.reshape(-1, 2))
