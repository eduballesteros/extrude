"""Modelo sintético para la Fase 3: cuatro edificios (dos al norte y dos al sur), uno con patio,
y casetas instanciadas; lejos del origen para comprobar que no salen coordenadas absolutas."""

from __future__ import annotations

from pathlib import Path

import ezdxf

from extrude.config import parse_config
from extrude.convert import ConvertOptions, convert
from extrude.node_tools import Compression

FAR_X = 431_000.0  # como un UTM: ninguna coordenada así puede aparecer en el contexto
FAR_Y = 4_582_000.0


# Quioscos instanciados: (escala, giro en grados) de cada copia del bloque KIOSCO (4 × 2 m)
KIOSKS = [(1.0, 0.0), (1.0, 30.0), (2.0, 0.0), (2.0, 90.0)]


def write_model(
    out: Path,
    *,
    extra_building: bool = False,
    kiosks: bool = False,
    doubles: bool = False,
    tiles: float | None = None,
    compression: Compression | None = None,
) -> Path:
    """Convierte el dibujo a `out` (model.glb + objects.json) y devuelve `out`."""
    doc = ezdxf.new()
    doc.units = 6
    msp = doc.modelspace()

    def square(x: float, y: float, size: float = 10, layer: str = "EDIFICIOS") -> None:
        msp.add_lwpolyline(
            [
                (FAR_X + x, FAR_Y + y),
                (FAR_X + x + size, FAR_Y + y),
                (FAR_X + x + size, FAR_Y + y + size),
                (FAR_X + x, FAR_Y + y + size),
            ],
            close=True,
            dxfattribs={"layer": layer},
        )

    square(0, 60)  # noroeste
    square(50, 60)  # noreste
    square(0, 0)  # suroeste
    patio = msp.add_hatch(dxfattribs={"layer": "EDIFICIOS"})  # sureste, con patio
    patio.paths.add_polyline_path(
        [
            (FAR_X + 50, FAR_Y),
            (FAR_X + 80, FAR_Y),
            (FAR_X + 80, FAR_Y + 20),
            (FAR_X + 50, FAR_Y + 20),
        ],
        flags=1,
    )
    patio.paths.add_polyline_path(
        [
            (FAR_X + 60, FAR_Y + 5),
            (FAR_X + 70, FAR_Y + 5),
            (FAR_X + 70, FAR_Y + 15),
            (FAR_X + 60, FAR_Y + 15),
        ],
        flags=0,
    )
    if extra_building:
        square(100, 30)
    block = doc.blocks.new("CASETA")
    block.add_lwpolyline([(0, 0), (2, 0), (2, 2), (0, 2)], close=True)
    for i in range(3):
        msp.add_blockref(
            "CASETA", (FAR_X + 20 + 4 * i, FAR_Y + 30), dxfattribs={"layer": "CASETAS"}
        )
    if kiosks:
        kiosk = doc.blocks.new("KIOSCO")
        kiosk.add_lwpolyline([(0, 0), (4, 0), (4, 2), (0, 2)], close=True)
        for i, (scale, angle) in enumerate(KIOSKS):
            msp.add_blockref(
                "KIOSCO",
                (FAR_X + 100 + 20 * i, FAR_Y - 30),
                dxfattribs={
                    "layer": "KIOSCOS",
                    "xscale": scale,
                    "yscale": scale,
                    "rotation": angle,
                },
            )
    if doubles:
        # Bloque de DOS piezas (dos huellas) en 4 copias: una copia se mueve/duplica entera
        double = doc.blocks.new("DOBLE")
        double.add_lwpolyline([(0, 0), (4, 0), (4, 4), (0, 4)], close=True)
        double.add_lwpolyline([(6, 0), (10, 0), (10, 4), (6, 4)], close=True)
        for i in range(4):
            msp.add_blockref("DOBLE", (FAR_X + 20 * i, FAR_Y - 70), dxfattribs={"layer": "DOBLES"})
    msp.add_lwpolyline(
        [
            (FAR_X - 10, FAR_Y + 40),
            (FAR_X + 90, FAR_Y + 40),
            (FAR_X + 90, FAR_Y + 46),
            (FAR_X - 10, FAR_Y + 46),
        ],
        close=True,
        dxfattribs={"layer": "VIALES"},
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    source = out.parent / f"{out.name}.dxf"
    doc.saveas(source)
    config = parse_config(
        {
            "instancing_min": 4,  # instancia los 4 quioscos, no las 3 casetas
            "layers": [
                {"match": "EDIFICIOS", "kind": "building", "height": 12, "detect_holes": True},
                {"match": "CASETAS", "kind": "building", "height": 3},
                {"match": "KIOSCOS", "kind": "building", "height": 3},
                {"match": "DOBLES", "kind": "building", "height": 6, "detect_holes": False},
                {"match": "VIALES", "kind": "road"},
                {"match": "*", "kind": "ignore"},
            ],
        }
    )
    convert(source, ConvertOptions(config, out, tiles, compression))
    return out
