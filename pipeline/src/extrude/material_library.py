"""Biblioteca de materiales controlada (§7 de docs/REQUISITOS_MATERIALIDAD_IA.md).

Los 12 presets mínimos con parámetros PBR y, salvo vidrio y agua, una textura de color base
procedural generada por Extrude (textures.py): sin licencias de terceros y autocontenida en el
GLB. Texturas fotográficas, normal maps y su presupuesto de memoria siguen pendientes (§16.2).

Un plan de materialidad solo puede referirse a materiales de esta biblioteca; un ID o versión
desconocidos se rechazan (RS-07). Cambiar los parámetros de un preset exige subir su versión.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

LIBRARY_VERSION = "library-1"
MATERIAL_ID = re.compile(r"^[a-z][a-z0-9_]{1,47}$")


@dataclass(frozen=True, slots=True)
class MaterialPreset:
    id: str
    version: str
    name: str  # nombre visible (español)
    family: str
    description: str
    color: str  # #rrggbb, sRGB
    roughness: float
    metalness: float = 0.0
    opacity: float = 1.0
    tile_size_m: tuple[float, float] = (1.0, 1.0)  # ancho × alto reales que representa
    pattern: str | None = None  # textura procedural (textures.PATTERNS); None = solo color

    @property
    def key(self) -> tuple[str, str]:
        return (self.id, self.version)


PRESETS: tuple[MaterialPreset, ...] = (
    MaterialPreset(
        "brick_light",
        "1",
        "Ladrillo claro",
        "ladrillo",
        "Fábrica de ladrillo de tono claro",
        "#d8c3a5",
        0.85,
        tile_size_m=(2.0, 2.0),
        pattern="brick",
    ),
    MaterialPreset(
        "brick_red",
        "1",
        "Ladrillo rojo",
        "ladrillo",
        "Fábrica de ladrillo rojo",
        "#9c4a35",
        0.85,
        tile_size_m=(2.0, 2.0),
        pattern="brick",
    ),
    MaterialPreset(
        "concrete_exposed",
        "1",
        "Hormigón visto",
        "hormigón",
        "Hormigón visto con encofrado",
        "#a8a69f",
        0.8,
        tile_size_m=(2.4, 1.2),
        pattern="concrete",
    ),
    MaterialPreset(
        "render_white",
        "1",
        "Revestimiento blanco",
        "revestimiento",
        "Enfoscado o revoco blanco",
        "#ecebe6",
        0.9,
        tile_size_m=(2.0, 2.0),
        pattern="render",
    ),
    MaterialPreset(
        "stone_grey",
        "1",
        "Piedra gris",
        "piedra",
        "Pavimento o aplacado de piedra gris",
        "#8e8d88",
        0.75,
        tile_size_m=(1.2, 1.2),
        pattern="stone",
    ),
    MaterialPreset(
        "asphalt",
        "1",
        "Asfalto",
        "pavimento",
        "Aglomerado asfáltico",
        "#3c3d3f",
        0.95,
        tile_size_m=(4.0, 4.0),
        pattern="asphalt",
    ),
    MaterialPreset(
        "roof_gravel",
        "1",
        "Grava de cubierta",
        "cubierta",
        "Cubierta plana acabada en grava",
        "#9d9a92",
        1.0,
        tile_size_m=(2.0, 2.0),
        pattern="gravel",
    ),
    MaterialPreset(
        "wood_exterior",
        "1",
        "Madera exterior",
        "madera",
        "Tablero o lama de madera para exterior",
        "#8a6542",
        0.7,
        tile_size_m=(2.0, 1.0),
        pattern="wood",
    ),
    MaterialPreset(
        "metal_dark",
        "1",
        "Metal oscuro",
        "metal",
        "Chapa o perfilería metálica oscura",
        "#2f3133",
        0.45,
        metalness=0.9,
        tile_size_m=(1.0, 1.0),
        pattern="seams",
    ),
    MaterialPreset(
        "glass_blue",
        "1",
        "Vidrio azulado",
        "vidrio",
        "Aproximación de vidrio: transparencia y rugosidad, sin refracción",
        "#9fb8c8",
        0.1,
        opacity=0.55,
        tile_size_m=(1.5, 3.0),
    ),
    MaterialPreset(
        "vegetation_generic",
        "1",
        "Vegetación genérica",
        "vegetación",
        "Paleta para geometría vegetal existente; no genera árboles",
        "#5f7a45",
        0.9,
        tile_size_m=(4.0, 4.0),
        pattern="foliage",
    ),
    MaterialPreset(
        "water",
        "1",
        "Agua",
        "agua",
        "Lámina de agua",
        "#4f7f96",
        0.15,
        opacity=0.85,
        tile_size_m=(8.0, 8.0),
    ),
)


class MaterialLibrary:
    def __init__(self, presets: tuple[MaterialPreset, ...], version: str) -> None:
        self.version = version
        self._by_key = {p.key: p for p in presets}
        self._ids = {p.id for p in presets}
        if len(self._by_key) != len(presets):
            raise ValueError("Biblioteca con materiales duplicados (id + versión)")
        bad = [p.id for p in presets if not MATERIAL_ID.match(p.id)]
        if bad:
            raise ValueError(f"IDs de material no válidos: {bad}")

    def get(self, material_id: str, version: str) -> MaterialPreset | None:
        return self._by_key.get((material_id, version))

    def has_id(self, material_id: str) -> bool:
        return material_id in self._ids

    @property
    def presets(self) -> list[MaterialPreset]:
        return list(self._by_key.values())


DEFAULT_LIBRARY = MaterialLibrary(PRESETS, LIBRARY_VERSION)
