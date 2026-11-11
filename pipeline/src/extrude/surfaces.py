"""Superficies semánticas de cada triángulo, asignadas al construir la geometría (RG-01).

Se etiquetan donde se crean (tapa, pared exterior, pared de patio, zócalo…), no a posteriori por
la normal: una cubierta inclinada o un patio no se distinguen bien de otra forma.
"""

from __future__ import annotations

from enum import IntEnum


class Surface(IntEnum):
    GENERIC = 0  # malla 3D de origen sin clasificar
    FACADE_EXTERIOR = 1
    FACADE_COURTYARD = 2  # paredes de los huecos interiores (patios)
    ROOF = 3
    FOUNDATION = 4  # zócalo automático: del terreno a la cota de planta
    GROUND = 5  # superficies sin altura (viales, agua) y terreno
    UNDERSIDE = 6  # tapa inferior opcional (bottom_cap)

    @property
    def key(self) -> str:
        """Nombre en `objects.json` y `materials.json`."""
        return self.name.lower()

    @classmethod
    def from_key(cls, key: str) -> Surface:
        return cls[key.upper()]


SURFACE_KEYS: tuple[str, ...] = tuple(s.key for s in Surface)
