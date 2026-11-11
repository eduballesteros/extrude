"""Texturas procedurales de la biblioteca: generadas por Extrude, sin recursos externos.

Cada textura es un patrón de **luminancia** (PNG en escala de grises, sRGB) que se repite sin
costuras y representa exactamente `tile_size_m` del preset: el color lo pone el factor del
material (baseColorFactor), así un cambio de color no requiere otra imagen. Al generarse aquí no
hay licencia de terceros que revisar (§7.1) y el resultado es determinista: la misma versión de
Extrude produce los mismos bytes.

Solo color base. Los normal maps y mapas de rugosidad quedan para cuando se apruebe el catálogo
con texturas reales (§16.2); el vidrio y el agua no llevan textura: un dibujo de carpinterías
simularía huecos que no existen en la geometría (§1).
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Callable
from functools import cache

import numpy as np
from numpy.typing import NDArray

TEXTURE_SIZE = 512  # px; punto de partida del presupuesto (§12 RN-08)
Image = NDArray[np.float64]  # (size, size) luminancia en [0, 1], fila 0 = v máxima (arriba)


def _noise(size: int, cells: int, rng: np.random.Generator) -> Image:
    """Ruido de valor periódico (se repite sin costura) con interpolación bilineal."""
    grid = rng.random((cells, cells))
    t = np.arange(size) * cells / size
    i0 = np.floor(t).astype(int) % cells
    i1 = (i0 + 1) % cells
    f = t - np.floor(t)
    f = f * f * (3 - 2 * f)
    rows = grid[i0][:, i0] * (1 - f)[None, :] + grid[i0][:, i1] * f[None, :]
    rows1 = grid[i1][:, i0] * (1 - f)[None, :] + grid[i1][:, i1] * f[None, :]
    result: Image = rows * (1 - f)[:, None] + rows1 * f[:, None]
    return result


def _fbm(size: int, rng: np.random.Generator, base: int = 4, octaves: int = 5) -> Image:
    total = np.zeros((size, size))
    weight = 0.0
    for octave in range(octaves):
        amplitude = 0.5**octave
        total += amplitude * _noise(size, base * 2**octave, rng)
        weight += amplitude
    result: Image = total / weight
    return result


def _coords(size: int) -> tuple[Image, Image]:
    """u, v en [0, 1) por píxel; v = 0 abajo (convención de UV de las paredes)."""
    u = (np.arange(size) + 0.5) / size
    v = 1 - (np.arange(size) + 0.5) / size
    uu, vv = np.meshgrid(u, v)
    return uu, vv


def _joints(t: Image, count: float, width: float) -> Image:
    """1 en las juntas (líneas cada 1/count), 0 fuera."""
    frac = (t * count) % 1.0
    return ((frac < width) | (frac > 1 - width)).astype(np.float64)


def brick(rng: np.random.Generator, size: int) -> Image:
    # Tile de 2 × 2 m: 8 ladrillos de 25 cm por hilada y 30 hiladas de 6,7 cm, a soga
    courses, per_row = 30, 8
    u, v = _coords(size)
    row = np.floor(v * courses).astype(int)
    shifted = u + (row % 2) * 0.5 / per_row
    column = np.floor(shifted * per_row).astype(int) % per_row
    tint = rng.uniform(0.62, 0.92, (courses, per_row))[row % courses, column]
    face = tint * (0.88 + 0.12 * _fbm(size, rng, 16, 3))
    mortar = np.maximum(_joints(v, courses, 0.09), _joints(shifted, per_row, 0.025))
    return np.where(mortar > 0, 1.0, face)


def concrete(rng: np.random.Generator, size: int) -> Image:
    # Tile de 2,4 × 1,2 m: un paño de encofrado con su junta y 6 latiguillos
    u, v = _coords(size)
    base = 0.86 + 0.12 * (_fbm(size, rng, 4, 6) - 0.5)
    joint = np.maximum(_joints(u, 1, 0.004), _joints(v, 1, 0.008))
    holes = np.zeros_like(u)
    for cu in (1 / 6, 0.5, 5 / 6):
        for cv in (0.25, 0.75):
            holes += ((u - cu) ** 2 * 4 + (v - cv) ** 2) < 0.012**2
    return np.clip(base - 0.18 * joint - 0.35 * np.clip(holes, 0, 1), 0, 1)


def stone(rng: np.random.Generator, size: int) -> Image:
    # Tile de 1,2 × 1,2 m: 2 × 2 losas de 60 cm
    u, v = _coords(size)
    slab = (np.floor(u * 2).astype(int) + 2 * np.floor(v * 2).astype(int)) % 4
    tint = rng.uniform(0.85, 1.0, 4)[slab]
    face = tint * (0.88 + 0.12 * _fbm(size, rng, 8, 5))
    joint = np.maximum(_joints(u, 2, 0.006), _joints(v, 2, 0.006))
    return np.where(joint > 0, 0.6, face)


def speckle(contrast: float, cells: int) -> Callable[[np.random.Generator, int], Image]:
    def make(rng: np.random.Generator, size: int) -> Image:
        grain = rng.random((size, size))
        return np.clip(0.9 + contrast * (0.6 * _fbm(size, rng, cells, 4) + 0.4 * grain - 0.5), 0, 1)

    return make


def wood(rng: np.random.Generator, size: int) -> Image:
    # Tile de 2 × 1 m: 10 tablas horizontales de 10 cm
    _, v = _coords(size)
    board = np.floor(v * 10).astype(int)
    tint = rng.uniform(0.8, 1.0, 10)[board % 10]
    grain = _noise(size, 64, rng)[:, :] * 0.5 + 0.5 * np.sin(
        (v * 10 % 1) * 40 + 6 * _fbm(size, rng)
    )
    face = tint * (0.85 + 0.1 * grain)
    return np.where(_joints(v, 10, 0.03) > 0, 0.45, face)


def seams(rng: np.random.Generator, size: int) -> Image:
    # Tile de 1 × 1 m: dos bandejas de 50 cm con junta alzada
    u, _ = _coords(size)
    base = 0.9 + 0.06 * (_fbm(size, rng, 4, 4) - 0.5)
    return np.where(_joints(u, 2, 0.01) > 0, 1.0, base)


PATTERNS: dict[str, Callable[[np.random.Generator, int], Image]] = {
    "brick": brick,
    "concrete": concrete,
    "stone": stone,
    "asphalt": speckle(0.35, 32),
    "gravel": speckle(0.6, 64),
    "render": speckle(0.08, 8),
    "wood": wood,
    "seams": seams,
    "foliage": speckle(0.5, 16),
}


def encode_png_gray(image: Image) -> bytes:
    """PNG de 8 bits en escala de grises (sin dependencias: zlib de la librería estándar)."""
    pixels = np.clip(np.round(image * 255), 0, 255).astype(np.uint8)
    height, width = pixels.shape
    raw = b"".join(b"\x00" + pixels[y].tobytes() for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _srgb_to_linear(x: Image) -> Image:
    result: Image = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
    return result


@cache
def texture(pattern: str, size: int = TEXTURE_SIZE) -> tuple[bytes, float]:
    """(PNG, luminancia media lineal). La media permite compensar el factor de color para que
    el tono medio del material coincida con el color del preset."""
    seed = zlib.crc32(pattern.encode("utf-8"))
    image = np.clip(PATTERNS[pattern](np.random.default_rng(seed), size), 0, 1)
    png = encode_png_gray(image)
    quantized = np.round(image * 255) / 255
    return png, float(_srgb_to_linear(quantized).mean())
