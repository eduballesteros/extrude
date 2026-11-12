"""Biblioteca de elementos del entorno colocables (vegetación y mobiliario).

Modelos procedurales propios de Extrude (licencia del proyecto, sin recursos de terceros) y
GENÉRICOS: no representan especies ni productos concretos. Cada recurso declara ID, versión,
dimensiones en metros, materiales, punto de apoyo (origen en la base, centro en planta) y
orientación (su «frente» mira a +Y local, el norte del plano antes de girarlo).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import Point, Polygon, box

from extrude.extrusion import MeshData, concat, extrude, labeled
from extrude.surfaces import Surface

ASSET_LIBRARY_VERSION = "site-1"


@dataclass(frozen=True)
class Part:
    mesh: MeshData
    material: str  # clave en Asset.materials


@dataclass(frozen=True)
class Asset:
    id: str
    version: str
    name: str
    kind: str  # vegetation | furniture
    size_m: tuple[float, float, float]  # ancho (x), fondo (y), alto (z)
    materials: dict[str, tuple[str, float, float]]  # clave → (color, rugosidad, metalicidad)
    parts: tuple[Part, ...] = field(default_factory=tuple)
    license: str = "Modelo procedural propio de Extrude (genérico)"

    @property
    def block(self) -> str:
        return self.id.upper()

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "kind": self.kind,
            "sizeM": list(self.size_m),
            "materials": sorted(self.materials),
            "anchor": "base, centro en planta",
            "front": "+Y local (antes de girar)",
            "license": self.license,
        }


def _generic(mesh: MeshData) -> MeshData:
    return labeled(mesh, Surface.GENERIC)


def cylinder(radius: float, z0: float, height: float, segments: int = 10) -> MeshData:
    return _generic(
        extrude(Point(0, 0).buffer(radius, quad_segs=segments // 4 or 1), z0, height, bottom=True)
    )


def block(x0: float, y0: float, x1: float, y1: float, z0: float, height: float) -> MeshData:
    return _generic(extrude(box(x0, y0, x1, y1), z0, height, bottom=True))


def ellipsoid(cz: float, rx: float, rz: float, rings: int = 6, segments: int = 10) -> MeshData:
    """Esfera achatada/alargada centrada en (0, 0, cz), cerrada y con caras hacia fuera."""
    verts = [(0.0, 0.0, cz + rz)]
    for i in range(1, rings):
        phi = math.pi * i / rings
        for j in range(segments):
            theta = 2 * math.pi * j / segments
            verts.append(
                (
                    rx * math.sin(phi) * math.cos(theta),
                    rx * math.sin(phi) * math.sin(theta),
                    cz + rz * math.cos(phi),
                )
            )
    verts.append((0.0, 0.0, cz - rz))
    faces = []
    for j in range(segments):  # casquete superior
        faces.append((0, 1 + j, 1 + (j + 1) % segments))
    for i in range(rings - 2):
        a, b = 1 + i * segments, 1 + (i + 1) * segments
        for j in range(segments):
            j2 = (j + 1) % segments
            faces += [(a + j, b + j, b + j2), (a + j, b + j2, a + j2)]
    last = len(verts) - 1
    base = 1 + (rings - 2) * segments
    for j in range(segments):
        faces.append((base + j, last, base + (j + 1) % segments))
    v = np.array(verts, dtype=np.float64)
    f = np.array(faces, dtype=np.int64)
    # Normales hacia fuera: invierte si el primer triángulo apunta al centro
    a, b, c = v[f[0]]
    if np.dot(np.cross(b - a, c - a), a - np.array([0, 0, cz])) < 0:
        f = f[:, ::-1]
    return MeshData(v, f, np.full(len(f), Surface.GENERIC, dtype=np.uint8))


GREEN = ("#4f7a3a", 0.9, 0.0)
DARK_GREEN = ("#3b5e2c", 0.9, 0.0)
BARK = ("#6b4f36", 0.95, 0.0)
METAL = ("#3c4248", 0.5, 0.8)
WOOD = ("#9a6b43", 0.8, 0.0)

ASSETS: dict[str, Asset] = {
    a.id: a
    for a in (
        Asset(
            "tree_round",
            "1",
            "Árbol de copa (genérico)",
            "vegetation",
            (5.0, 5.0, 7.0),
            {"trunk": BARK, "crown": GREEN},
            (Part(cylinder(0.18, 0, 3.0), "trunk"), Part(ellipsoid(4.5, 2.5, 2.5), "crown")),
        ),
        Asset(
            "tree_columnar",
            "1",
            "Árbol de porte vertical (genérico)",
            "vegetation",
            (2.0, 2.0, 9.0),
            {"trunk": BARK, "crown": DARK_GREEN},
            (Part(cylinder(0.15, 0, 2.0), "trunk"), Part(ellipsoid(5.5, 1.0, 3.5), "crown")),
        ),
        Asset(
            "shrub",
            "1",
            "Arbusto (genérico)",
            "vegetation",
            (1.6, 1.6, 1.2),
            {"leaves": GREEN},
            (Part(ellipsoid(0.6, 0.8, 0.6), "leaves"),),
        ),
        Asset(
            "bench",
            "1",
            "Banco (genérico)",
            "furniture",
            (1.8, 0.6, 0.85),
            {"wood": WOOD, "metal": METAL},
            (
                Part(
                    concat(
                        [
                            block(-0.9, -0.3, 0.9, 0.15, 0.42, 0.06),  # asiento
                            block(-0.9, -0.3, 0.9, -0.24, 0.48, 0.37),
                        ]
                    ),
                    "wood",
                ),  # respaldo
                Part(
                    concat(
                        [
                            block(-0.8, -0.28, -0.74, 0.12, 0, 0.42),
                            block(0.74, -0.28, 0.8, 0.12, 0, 0.42),
                        ]
                    ),
                    "metal",
                ),
            ),
        ),
        Asset(
            "street_lamp",
            "1",
            "Farola (genérica)",
            "furniture",
            (0.6, 0.6, 4.5),
            {"metal": METAL},
            (
                Part(
                    concat([cylinder(0.07, 0, 4.2), block(-0.3, -0.15, 0.3, 0.15, 4.2, 0.2)]),
                    "metal",
                ),
            ),
        ),
        Asset(
            "litter_bin",
            "1",
            "Papelera (genérica)",
            "furniture",
            (0.5, 0.5, 0.9),
            {"metal": METAL},
            (Part(cylinder(0.25, 0, 0.9), "metal"),),
        ),
    )
}


def footprint(asset: Asset, scale: float) -> Polygon:
    w, d, _ = asset.size_m
    return box(-w * scale / 2, -d * scale / 2, w * scale / 2, d * scale / 2)
