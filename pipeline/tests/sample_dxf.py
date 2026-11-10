"""Generadores de DXF sintéticos para tests y para `samples/`.

Uso: `uv run python tests/sample_dxf.py ../samples`

Las constantes `BUILDINGS`, `UTM_OFFSET`, etc. describen la geometría esperada para que los
tests de fases posteriores (alturas, volúmenes, huecos) comparen contra valores conocidos.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
import numpy as np
from ezdxf import const, units
from ezdxf.acis import api as acis
from ezdxf.document import Drawing
from ezdxf.enums import TextEntityAlignment
from ezdxf.layouts import Modelspace
from ezdxf.render import forms

Point2 = tuple[float, float]


@dataclass(frozen=True)
class BuildingSpec:
    name: str
    outer: Sequence[Point2]
    floors: int
    label_at: Point2
    holes: Sequence[Sequence[Point2]] = field(default_factory=tuple)
    bulges: Sequence[float] | None = None  # una por vértice de `outer` (LWPOLYLINE)


BUILDINGS: tuple[BuildingSpec, ...] = (
    BuildingSpec("A", ((0, 0), (20, 0), (20, 12), (0, 12)), floors=4, label_at=(10, 6)),
    BuildingSpec("B", ((25, 0), (45, 0), (45, 15), (25, 15)), floors=6, label_at=(35, 7.5)),
    BuildingSpec(
        "C_patio",
        ((0, 20), (30, 20), (30, 40), (0, 40)),
        floors=3,
        label_at=(5, 30),
        holes=(((10, 25), (20, 25), (20, 35), (10, 35)),),
    ),
    # Lado superior curvo: bulge 0.5 en el tramo (50,35)→(35,35)
    BuildingSpec(
        "D_curvo",
        ((35, 20), (50, 20), (50, 35), (35, 35)),
        floors=2,
        label_at=(42.5, 27.5),
        bulges=(0.0, 0.0, 0.5, 0.0),
    ),
)
ROAD: tuple[Point2, ...] = ((-5, -10), (60, -10), (60, -3), (-5, -3))
WATER: tuple[Point2, ...] = ((55, 20), (70, 20), (70, 40), (55, 40))
UTM_OFFSET: Point2 = (230_000.0, 4_140_000.0)

TREE_RADIUS = 1.5
TREE_GRID = (5, 4)
GROUP_TREES = 3
GROUP_INSERTS = 3
MIRRORED_TREE_OCS_X = 100.0  # con extrusión (0,0,-1) el centro WCS queda en x = -100
TOTAL_TREES = TREE_GRID[0] * TREE_GRID[1] + GROUP_TREES * GROUP_INSERTS + 1
TYPICAL_BUILDING_FLOORS = (3, 5, 7)

BOX_SIZE_MM = 10_000.0


def _new_doc(insunits: int = units.M) -> Drawing:
    doc = ezdxf.new("R2018", setup=True)
    doc.units = insunits
    return doc


def _label(msp: Modelspace, text: str, at: Point2, layer: str) -> None:
    msp.add_text(text, height=1.0, dxfattribs={"layer": layer}).set_placement(
        at, align=TextEntityAlignment.MIDDLE_CENTER
    )


def _shift(points: Sequence[Point2], offset: Point2) -> list[Point2]:
    return [(x + offset[0], y + offset[1]) for x, y in points]


def make_block_2d(offset: Point2 = (0.0, 0.0)) -> Drawing:
    """(a)/(b) Manzana 2D: edificios con textos de plantas, patio interior, vial y agua."""
    doc = _new_doc()
    for name in ("EDIFICIOS", "EDIFICIOS_TEXTO", "VIALES", "AGUA", "TEXTOS"):
        doc.layers.add(name)
    msp = doc.modelspace()
    for spec in BUILDINGS:
        outer = _shift(spec.outer, offset)
        if spec.bulges is None:
            msp.add_lwpolyline(outer, close=True, dxfattribs={"layer": "EDIFICIOS"})
        else:
            pts = [(x, y, 0, 0, b) for (x, y), b in zip(outer, spec.bulges, strict=True)]
            msp.add_lwpolyline(pts, format="xyseb", close=True, dxfattribs={"layer": "EDIFICIOS"})
        for hole in spec.holes:
            msp.add_lwpolyline(_shift(hole, offset), close=True, dxfattribs={"layer": "EDIFICIOS"})
        _label(msp, f"{spec.floors}P", _shift([spec.label_at], offset)[0], "EDIFICIOS_TEXTO")
    msp.add_lwpolyline(_shift(ROAD, offset), close=True, dxfattribs={"layer": "VIALES"})
    msp.add_lwpolyline(_shift(WATER, offset), close=True, dxfattribs={"layer": "AGUA"})
    _label(msp, "MANZANA 1", _shift([(25, -20)], offset)[0], "TEXTOS")
    return doc


def make_block_2d_utm() -> Drawing:
    return make_block_2d(UTM_OFFSET)


def make_nested_blocks() -> Drawing:
    """(c) Bloques anidados repetidos, con rotación, escala, OCS invertido y atributos."""
    doc = _new_doc()
    for name in ("VEGETACION", "MOBILIARIO", "EDIFICIOS"):
        doc.layers.add(name)

    tree = doc.blocks.new("ARBOL")
    tree.add_circle((0, 0), TREE_RADIUS, dxfattribs={"layer": "0"})
    tree.add_lwpolyline(
        [(-0.2, -0.2), (0.2, -0.2), (0.2, 0.2), (-0.2, 0.2)], close=True, dxfattribs={"layer": "0"}
    )

    group = doc.blocks.new("GRUPO_ARBOLES")
    for i in range(GROUP_TREES):
        group.add_blockref("ARBOL", (i * 4.0, 0), dxfattribs={"layer": "0"})
    group.add_line((0, -3), (8, -3), dxfattribs={"layer": "MOBILIARIO"})

    typical = doc.blocks.new("EDIF_TIPO")
    typical.add_lwpolyline([(0, 0), (10, 0), (10, 8), (0, 8)], close=True)
    typical.add_attdef("PLANTAS", (5, 4), dxfattribs={"height": 1.0})

    msp = doc.modelspace()
    cols, rows = TREE_GRID
    for i in range(cols):
        for j in range(rows):
            msp.add_blockref("ARBOL", (i * 6.0, j * 6.0), dxfattribs={"layer": "VEGETACION"})
    for k in range(GROUP_INSERTS):
        msp.add_blockref(
            "GRUPO_ARBOLES",
            (0, 40 + k * 12.0),
            dxfattribs={"layer": "VEGETACION", "rotation": 30, "xscale": 2, "yscale": 2},
        )
    msp.add_blockref(
        "ARBOL",
        (MIRRORED_TREE_OCS_X, 0),
        dxfattribs={"layer": "VEGETACION", "extrusion": (0, 0, -1)},
    )
    for k, floors in enumerate(TYPICAL_BUILDING_FLOORS):
        ref = msp.add_blockref("EDIF_TIPO", (k * 20.0, 90), dxfattribs={"layer": "EDIFICIOS"})
        ref.add_auto_attribs({"PLANTAS": str(floors)})
    return doc


def make_faces_3d() -> Drawing:
    """(d) 3DFACE (cubo), MESH (pirámide), polyface, SOLID 2D y entidades ACIS no soportadas.

    En milímetros ($INSUNITS = 4) para probar la detección de unidades.
    """
    doc = _new_doc(units.MM)
    for name in ("CUBO", "MALLA", "POLYFACE", "SOLID2D", "SOLIDOS"):
        doc.layers.add(name)
    msp = doc.modelspace()
    s = BOX_SIZE_MM
    v = [(0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0), (0, 0, s), (s, 0, s), (s, s, s), (0, s, s)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    for quad in quads:
        msp.add_3dface([v[i] for i in quad], dxfattribs={"layer": "CUBO"})

    mesh = msp.add_mesh(dxfattribs={"layer": "MALLA"})
    with mesh.edit_data() as data:
        data.vertices = [(20_000, 0, 0), (30_000, 0, 0), (30_000, 10_000, 0), (20_000, 10_000, 0)]
        data.vertices.append((25_000, 5_000, 8_000))
        data.faces = [(0, 3, 2, 1), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)]

    polyface = msp.add_polyface(dxfattribs={"layer": "POLYFACE"})
    polyface.append_face([(40_000, 0, 0), (50_000, 0, 0), (45_000, 8_000, 3_000)])

    msp.add_solid(
        [(0, -5_000), (5_000, -5_000), (0, -2_000), (5_000, -2_000)],
        dxfattribs={"layer": "SOLID2D"},
    )
    # ezdxf descarta al guardar las entidades ACIS vacías: se les da un cuerpo real
    body = acis.body_from_mesh(forms.cube().scale_uniform(1_000))
    for _ in range(2):
        acis.export_dxf(msp.add_3dsolid(dxfattribs={"layer": "SOLIDOS"}), [body])
    acis.export_dxf(msp.add_body(dxfattribs={"layer": "SOLIDOS"}), [body])
    return doc


DIRTY_CLOSE_GAP = 0.004  # < tolerancia por defecto (0,01 m)


def make_dirty() -> Drawing:
    """(e) Geometría "sucia": lazos abiertos, LINE/ARC sueltas, duplicados y degenerados."""
    doc = _new_doc()
    for name in ("EDIFICIOS", "ZONAS_VERDES", "AGUA", "SIN_REGLA"):
        doc.layers.add(name)
    msp = doc.modelspace()
    edif = {"layer": "EDIFICIOS"}

    # Polilínea abierta cuyo final está a 4 mm del inicio
    msp.add_lwpolyline(
        [(0, 0), (10, 0), (10, 10), (0, 10 - 0), (0, DIRTY_CLOSE_GAP)], dxfattribs=edif
    )
    # Rectángulo hecho con 4 LINE desordenadas y con sentidos mezclados
    for a, b in (
        ((20, 0), (30, 0)),
        ((20, 10), (20, 0)),
        ((30, 10), (20, 10)),
        ((30, 0), (30, 10)),
    ):
        msp.add_line(a, b, dxfattribs=edif)
    # Lazo LINE + ARC (esquina superior redondeada)
    msp.add_line((40, 0), (50, 0), dxfattribs=edif)
    msp.add_line((50, 0), (50, 5), dxfattribs=edif)
    msp.add_arc((45, 5), 5, 0, 180, dxfattribs=edif)
    msp.add_line((40, 5), (40, 0), dxfattribs=edif)
    # Duplicado exacto
    for _ in range(2):
        msp.add_lwpolyline([(60, 0), (70, 0), (70, 10), (60, 10)], close=True, dxfattribs=edif)
    # Pajarita autointersectada
    msp.add_lwpolyline([(80, 0), (90, 10), (90, 0), (80, 10)], close=True, dxfattribs=edif)
    # Área nula (colineal)
    msp.add_lwpolyline([(100, 0), (105, 0), (110, 0)], close=True, dxfattribs=edif)
    # LINE colgante que no cierra nada
    msp.add_line((0, 20), (15, 25), dxfattribs=edif)

    green = [(0, 40), (10, 35), (20, 40), (20, 50), (10, 55), (0, 50)]
    msp.add_spline(fit_points=[*green, green[0]], dxfattribs={"layer": "ZONAS_VERDES"})
    hatch = msp.add_hatch(color=5, dxfattribs={"layer": "AGUA"})
    hatch.paths.add_polyline_path(
        [(30, 30), (60, 30), (60, 60), (30, 60)], is_closed=True, flags=const.BOUNDARY_PATH_EXTERNAL
    )
    hatch.paths.add_polyline_path(
        [(40, 40), (50, 40), (50, 50), (40, 50)],
        is_closed=True,
        flags=const.BOUNDARY_PATH_OUTERMOST,
    )
    msp.add_text("capa sin regla", dxfattribs={"layer": "SIN_REGLA", "insert": (0, -5)})
    msp.add_aligned_dim(p1=(0, -10), p2=(10, -10), distance=1).render()
    return doc


CITY_BLOCK_M = 100.0  # manzana de 100 × 100 m con calle de 20 m alrededor
CITY_STREET_M = 20.0
CITY_BUILDINGS_PER_BLOCK = 16  # 4 × 4 parcelas
CITY_TREES_PER_BLOCK = 4


def make_city(buildings: int = 50_000, seed: int = 7, offset: Point2 = UTM_OFFSET) -> Drawing:
    """Ciudad sintética en UTM: manzanas de 4×4 edificios con rótulo "nP", calles y árboles."""
    import random

    rng = random.Random(seed)
    doc = _new_doc()
    for name in ("EDIFICIOS", "EDIFICIOS_TEXTO", "VIALES", "VEGETACION"):
        doc.layers.add(name)
    tree = doc.blocks.new("ARBOL")
    tree.add_circle((0, 0), TREE_RADIUS)
    msp = doc.modelspace()

    blocks = -(-buildings // CITY_BUILDINGS_PER_BLOCK)
    side = int(np.ceil(np.sqrt(blocks)))
    pitch = CITY_BLOCK_M + CITY_STREET_M
    plot = CITY_BLOCK_M / 4
    made = 0
    for b in range(blocks):
        bx = offset[0] + (b % side) * pitch
        by = offset[1] + (b // side) * pitch
        for k in range(CITY_BUILDINGS_PER_BLOCK):
            if made == buildings:
                break
            px, py = bx + (k % 4) * plot, by + (k // 4) * plot
            w, d = rng.uniform(12, plot - 2), rng.uniform(12, plot - 2)
            x0, y0 = px + (plot - w) / 2, py + (plot - d) / 2
            msp.add_lwpolyline(
                [(x0, y0), (x0 + w, y0), (x0 + w, y0 + d), (x0, y0 + d)],
                close=True,
                dxfattribs={"layer": "EDIFICIOS"},
            )
            msp.add_text(
                f"{rng.randint(1, 12)}P", height=1.0, dxfattribs={"layer": "EDIFICIOS_TEXTO"}
            ).set_placement((x0 + w / 2, y0 + d / 2), align=TextEntityAlignment.MIDDLE_CENTER)
            made += 1
        for t in range(CITY_TREES_PER_BLOCK):
            msp.add_blockref(
                "ARBOL",
                (bx - CITY_STREET_M / 2, by + t * CITY_BLOCK_M / CITY_TREES_PER_BLOCK),
                dxfattribs={"layer": "VEGETACION"},
            )
    width = side * pitch
    msp.add_lwpolyline(
        [
            (offset[0] - CITY_STREET_M, offset[1] - CITY_STREET_M),
            (offset[0] + width, offset[1] - CITY_STREET_M),
            (offset[0] + width, offset[1] + width),
            (offset[0] - CITY_STREET_M, offset[1] + width),
        ],
        close=True,
        dxfattribs={"layer": "VIALES"},
    )
    return doc


SAMPLES: dict[str, Callable[[], Drawing]] = {
    "block_2d.dxf": make_block_2d,
    "block_2d_utm.dxf": make_block_2d_utm,
    "nested_blocks.dxf": make_nested_blocks,
    "faces_3d.dxf": make_faces_3d,
    "dirty.dxf": make_dirty,
}


def write_samples(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, factory in SAMPLES.items():
        path = out_dir / filename
        factory().saveas(path)
        written.append(path)
    return written


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../samples")
    if len(sys.argv) > 2:  # python tests/sample_dxf.py <dir> <n>: ciudad de n edificios
        path = target / f"city_{sys.argv[2]}.dxf"
        make_city(int(sys.argv[2])).saveas(path)
        print(path)
    else:
        for p in write_samples(target):
            print(p)
