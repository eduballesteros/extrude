"""Genera los iconos de Extrude (SVG + PNG + ICO) desde una única geometría.

Estilo: monocromo, trazo negro grueso de grosor constante con uniones redondeadas sobre fondo
blanco (en la línea de Threads). Motivo: el contorno isométrico de un volumen extruido —una
planta que se levanta—, que es lo que hace la aplicación.
Uso:  uv run --no-project --with pillow==11.3.0 python viewer/scripts/make-icons.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

PUBLIC = Path(__file__).resolve().parents[1] / "public"
SIZE = 64
INK = "#000000"
PAPER = "#ffffff"
STROKE = 5.0  # grosor del trazo sobre 64 px
CONTENT = 34.0  # lado del área que ocupa el dibujo dentro de 64
RADIUS = 14  # esquinas del fondo

COS30, SIN30 = math.cos(math.radians(30)), 0.5
WIDTH, DEPTH, HEIGHT = 1.0, 1.0, 2.1  # torre: se lee como edificio incluso a 16 px

Point = tuple[float, float]


def iso(x: float, y: float, z: float) -> Point:
    return ((x - y) * COS30, (x + y) * SIN30 - z)


def paths() -> list[list[Point]]:
    """Contorno exterior (cerrado) + las tres aristas interiores que dan el volumen."""
    w, d, h = WIDTH, DEPTH, HEIGHT
    a, b, c, dd = iso(0, 0, h), iso(w, 0, h), iso(w, d, h), iso(0, d, h)
    e, f, g = iso(w, 0, 0), iso(w, d, 0), iso(0, d, 0)
    raw = [[a, b, e, f, g, dd, a], [dd, c, b], [c, f]]
    xs = [p[0] for path in raw for p in path]
    ys = [p[1] for path in raw for p in path]
    scale = CONTENT / max(max(xs) - min(xs), max(ys) - min(ys))
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    return [[(SIZE / 2 + (x - cx) * scale, SIZE / 2 + (y - cy) * scale) for x, y in p] for p in raw]


def svg(background: bool = True) -> str:
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}">', "<title>Extrude</title>"]
    if background:
        parts.append(f'<rect width="{SIZE}" height="{SIZE}" rx="{RADIUS}" fill="{PAPER}"/>')
    parts.append(
        f'<g fill="none" stroke="{INK}" stroke-width="{STROKE}" '
        'stroke-linejoin="round" stroke-linecap="round">'
    )
    for path in paths():
        pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in path)
        parts.append(f'<polyline points="{pts}"/>')
    parts.append("</g></svg>")
    return "\n".join(parts) + "\n"


def png(size: int, *, rounded: bool = True, stroke: float = STROKE) -> Image.Image:
    """Rasterizado con sobremuestreo ×8; uniones redondeadas con círculos en los vértices."""
    k = size * 8 / SIZE
    big = Image.new("RGBA", (size * 8, size * 8), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    draw.rounded_rectangle(
        (0, 0, size * 8 - 1, size * 8 - 1), radius=int(RADIUS * k) if rounded else 0, fill=PAPER
    )
    width = stroke * k
    for path in paths():
        pts = [(x * k, y * k) for x, y in path]
        draw.line(pts, fill=INK, width=round(width))
        for x, y in pts:
            r = width / 2
            draw.ellipse((x - r, y - r, x + r, y + r), fill=INK)
    return big.resize((size, size), Image.LANCZOS)


def main() -> None:
    (PUBLIC / "favicon.svg").write_text(svg(), encoding="utf-8")
    (PUBLIC / "logo-mark.svg").write_text(svg(background=False), encoding="utf-8")
    # A 16 px el trazo se engrosa un poco para que no se pierda
    icons = [png(16, stroke=6.0), png(32), png(48)]
    icons[0].save(PUBLIC / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)], append_images=icons[1:])
    png(180, rounded=False).save(PUBLIC / "apple-touch-icon.png")
    png(512).save(PUBLIC / "icon-512.png")
    print("Iconos escritos en", PUBLIC)


if __name__ == "__main__":
    main()
