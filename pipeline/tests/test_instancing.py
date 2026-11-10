from __future__ import annotations

from pathlib import Path

import ezdxf
from ezdxf import units

from extrude.build import build_scene
from extrude.config import parse_config
from extrude.dxf_reader import read_dxf
from extrude.instancing import plan_instances

COPIES = 12


def _tree_dwg(tmp_path: Path) -> Path:
    """Árbol con arcos (nube) insertado a escalas y giros distintos, como en planos reales."""
    doc = ezdxf.new("R2018")
    doc.units = units.M
    block = doc.blocks.new("ARBOL")
    cloud = [(1, 0, 0.6), (0, 1, 0.6), (-1, 0, 0.6), (0, -1, 0.6)]  # x, y, bulge
    block.add_lwpolyline(
        [(x, y, 0, 0, b) for x, y, b in cloud],
        format="xyseb",
        close=True,
        dxfattribs={"layer": "0"},
    )
    block.add_hatch().paths.add_polyline_path([(x, y, b) for x, y, b in cloud], is_closed=True)
    msp = doc.modelspace()
    for i in range(COPIES):
        scale = 1 + 0.17 * i  # cada escala aplana los arcos con distinto nº de vértices
        msp.add_blockref(
            "ARBOL",
            (i * 10.0, 0),
            dxfattribs={
                "layer": "ARBOLES",
                "xscale": scale,
                "yscale": scale,
                "zscale": scale,
                "rotation": 23 * i,
            },
        )
    path = tmp_path / "arboles.dxf"
    doc.saveas(path)
    return path


def test_scaled_curved_blocks_are_instanced(tmp_path: Path) -> None:
    config = parse_config(
        {"layers": [{"match": "ARBOLES", "kind": "vegetation", "height": 5, "detect_holes": False}]}
    )
    read = read_dxf(_tree_dwg(tmp_path))
    layer = build_scene(read, config).layers["ARBOLES"]
    assert layer.stats.topology.duplicates_removed == COPIES  # hatch = contorno
    vertex_counts = {len(o.mesh.vertices) for o in layer.objects}
    assert len(vertex_counts) > 1  # densidad distinta según la escala
    groups, loose = plan_instances(layer.objects, scale=1.0, min_count=10, tolerance=0.01)
    assert [len(g.matrices) for g in groups] == [COPIES]
    assert loose == []
    # La altura del prototipo sigue siendo la real (5 m), no escalada por el INSERT
    proto_z = groups[0].prototype.vertices[:, 2]
    assert proto_z.max() - proto_z.min() == 5
    assert all(abs(m[2, 2] - 1) < 1e-12 for m in groups[0].matrices)


def test_attribute_heights_still_block_instancing(samples_dir: Path) -> None:
    config = parse_config(
        {
            "instancing_min": 2,
            "layers": [
                {
                    "match": "EDIFICIOS",
                    "kind": "building",
                    "height_from": "attribute",
                    "attribute": "PLANTAS",
                }
            ],
        }
    )
    layer = build_scene(read_dxf(samples_dir / "nested_blocks.dxf"), config).layers["EDIFICIOS"]
    groups, loose = plan_instances(layer.objects, scale=1.0, min_count=2, tolerance=0.01)
    assert groups == []
    assert len(loose) == 3
