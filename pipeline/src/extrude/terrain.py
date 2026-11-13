"""Superficie del terreno (TIN) para apoyar en ella las capas sin cota (`base_from_terrain`).

- Edificios: planta horizontal sobre la cota máxima y zócalo hasta el terreno.
- Bloques (INSERT) en terreno casi llano (desnivel ≤ FLAT_RELIEF_M): igual, base plana, así la
  copia sigue siendo instanciable (misma forma, solo cambia la Z).
- El resto (viales, agua, vegetación, bloques en pendiente) se "drapea": la huella se trocea con
  los triángulos del TIN y cada vértice toma la cota del terreno, siguiendo la pendiente.
Fuera del TIN se usa la cota del vértice de terreno más cercano.

El zócalo de un edificio se separa de la fachada en la cota de planta (RG-02): cada pared se
parte en dos quads coplanares, terreno → planta (FOUNDATION) y planta → cubierta (fachada). La
envolvente no cambia; solo se añade la arista horizontal de la planta.
"""

from __future__ import annotations

import numpy as np
import shapely
from numpy.typing import NDArray
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
from scipy.spatial import Delaunay, QhullError
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry

from extrude.extrusion import (
    MeshData,
    concat,
    drop_degenerate,
    extrude,
    labeled,
    ring_surface,
    wall_quads,
)
from extrude.surfaces import Surface

MIN_PIECE_AREA = 1e-8  # m²
MIN_TERRAIN_POINTS = 3
FLAT_RELIEF_M = 1.0
GROUND_TOLERANCE_M = 1.0  # base sobre el terreno o cubierta bajo él: más de esto es un error


class TerrainSurface:
    def __init__(self, points: NDArray[np.float64]) -> None:
        """`points`: (N, 3) en metros; se triangula en XY (Delaunay)."""
        xy, z = points[:, :2], points[:, 2]
        self._delaunay = Delaunay(xy)
        self._linear = LinearNDInterpolator(self._delaunay, z)
        self._nearest = NearestNDInterpolator(xy, z)
        self._triangles = shapely.polygons(xy[self._delaunay.simplices])
        self._boundaries = shapely.boundary(self._triangles)
        self._tree = shapely.STRtree(self._triangles)
        self._hull = shapely.convex_hull(shapely.multipoints(xy))

    @staticmethod
    def from_points(points: NDArray[np.float64]) -> TerrainSurface | None:
        if len(points) < MIN_TERRAIN_POINTS:
            return None
        try:
            return TerrainSurface(points)
        except QhullError:  # puntos colineales
            return None

    @property
    def z_range(self) -> tuple[float, float]:
        z = self._linear.values[:, 0]
        return float(z.min()), float(z.max())

    def height(self, xy: NDArray[np.float64]) -> NDArray[np.float64]:
        xy = np.asarray(xy, dtype=np.float64)[:, :2]
        z: NDArray[np.float64] = np.asarray(self._linear(xy), dtype=np.float64)
        outside = np.isnan(z)
        if outside.any():
            z[outside] = self._nearest(xy[outside])
        return z

    def covers(self, polygon: Polygon) -> bool:
        """False si parte de la huella usa la extrapolación por vecino más cercano."""
        return bool(self._hull.covers(polygon))

    def range_under(self, polygon: Polygon) -> tuple[float, float]:
        """Cotas mínima y máxima exactas del terreno sobre la huella.

        El TIN es lineal a trozos: sobre un polígono, sus extremos están en los vértices del TIN
        interiores o en el contorno, en sus vértices o donde lo cortan las aristas del TIN (un
        valle o una cresta que cruza el borde). Se muestrean los tres.
        """
        candidates = self._tree.query(polygon, predicate="intersects")
        rings = [polygon.exterior, *polygon.interiors]
        z = [self.height(self._densify(LineString(ring.coords), candidates)) for ring in rings]
        inside = self._linear.points[shapely.contains_xy(polygon, *self._linear.points.T)]
        if len(inside):
            z.append(self.height(inside))
        values = np.concatenate(z)
        return float(values.min()), float(values.max())

    def drape(self, polygon: Polygon, offset: float, height: float) -> MeshData:
        """Superficie (y paredes si `height` > 0) que sigue el terreno: cota = terreno + offset."""
        candidates = self._tree.query(polygon, predicate="intersects")
        pieces = self._pieces(polygon, candidates)
        top = Surface.ROOF if height > 0 else Surface.GROUND
        parts = [labeled(self._draped_cap(piece, offset + height), top) for piece in pieces]
        if height > 0:
            rings = [polygon.exterior, *polygon.interiors]
            parts += [
                self._draped_wall(ring, candidates, offset, height, ring_surface(i))
                for i, ring in enumerate(rings)
            ]
        return concat([p for p in parts if p.triangle_count])

    def building(
        self, polygon: Polygon, floor: float, height: float, offset: float, *, bottom: bool
    ) -> MeshData:
        """Cubierta horizontal; zócalo que cierra hasta el TIN sin modificarlo.

        `floor` es la cota máxima del terreno más offset; height se mide desde esa planta,
        no desde el extremo bajo del zócalo. La tapa inferior opcional sigue el TIN.
        """
        candidates = self._tree.query(polygon, predicate="intersects")
        # Densificar también la cubierta evita uniones en T en las paredes y patios.
        rings = [
            self._densify(LineString(r.coords), candidates)
            for r in [polygon.exterior, *polygon.interiors]
        ]
        outline = Polygon(rings[0], rings[1:])
        pieces = self._pieces(outline, candidates)
        parts = [
            labeled(extrude(piece, floor + height, 0.0, bottom=False), Surface.ROOF)
            for piece in pieces
        ]
        for i, xy in enumerate(rings):
            ground = self.height(xy) + offset
            plan = np.full(len(xy), floor)
            # Zócalo terreno → planta; donde el terreno toca la planta sus triángulos son
            # degenerados: se quitan aquí para no dejar caras sin área en la malla cerrada.
            foundation = _walls(xy, np.minimum(ground, plan), plan, Surface.FOUNDATION)
            parts.append(drop_degenerate(foundation)[0])
            if height > 0:
                parts.append(_walls(xy, plan, plan + height, ring_surface(i)))
        if bottom:
            for piece in pieces:
                cap = self._draped_cap(piece, offset)
                parts.append(labeled(MeshData(cap.vertices, cap.faces[:, ::-1]), Surface.UNDERSIDE))
        return concat(parts)

    def _pieces(self, polygon: Polygon, candidates: NDArray[np.int64]) -> list[Polygon]:
        pieces = _polygons(shapely.intersection(polygon, self._triangles[candidates]))
        if not self._hull.contains(polygon):
            pieces += _polygons(polygon.difference(self._hull))
        return pieces

    def _draped_cap(self, piece: Polygon, offset: float) -> MeshData:
        flat = extrude(piece, 0.0, 0.0, bottom=False)
        vertices = flat.vertices.copy()
        vertices[:, 2] = self.height(vertices) + offset
        return MeshData(vertices, flat.faces)

    def _draped_wall(
        self,
        ring: BaseGeometry,
        candidates: NDArray[np.int64],
        offset: float,
        height: float,
        surface: Surface,
    ) -> MeshData:
        xy = self._densify(LineString(ring.coords), candidates)
        bottom = self.height(xy) + offset
        return _walls(xy, bottom, bottom + height, surface)

    def _densify(self, line: LineString, candidates: NDArray[np.int64]) -> NDArray[np.float64]:
        """Vértices del anillo + cortes con las aristas del TIN (sin repetir el cierre)."""
        coords = np.asarray(line.coords)[:, :2]
        steps = np.linalg.norm(np.diff(coords, axis=0), axis=1)
        distances = [np.concatenate([[0.0], np.cumsum(steps)])]
        if len(candidates):
            cuts = shapely.get_coordinates(shapely.intersection(line, self._boundaries[candidates]))
            if len(cuts):
                distances.append(shapely.line_locate_point(line, shapely.points(cuts)))
        d = np.unique(np.round(np.concatenate(distances), 9))
        d = d[d < line.length - 1e-9]
        points: NDArray[np.float64] = shapely.get_coordinates(
            shapely.line_interpolate_point(line, d)
        )
        return points


def _polygons(geometry: BaseGeometry | NDArray[np.object_]) -> list[Polygon]:
    out: list[Polygon] = []
    for g in np.atleast_1d(np.asarray(geometry, dtype=object)):
        for part in shapely.get_parts(g):
            if isinstance(part, Polygon) and part.area > MIN_PIECE_AREA:
                out.append(part)
    return out


def _walls(
    ring: NDArray[np.float64],
    bottom: NDArray[np.float64],
    top: NDArray[np.float64],
    surface: Surface,
) -> MeshData:
    """Un quad por arista con cotas propias en cada extremo (mismo convenio que extrusion)."""
    return wall_quads(ring, bottom, top, surface)
