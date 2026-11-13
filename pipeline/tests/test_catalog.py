"""Fase 0 de materialidad: IDs persistentes, etiquetas, revisión geométrica y superficies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import ezdxf
import numpy as np
import pytest
from ezdxf.document import Drawing

from extrude.build import build_scene
from extrude.catalog import CATALOG_NAME, source_key
from extrude.config import Config, parse_config
from extrude.convert import ConvertOptions, convert
from extrude.dxf_reader import read_dxf
from extrude.gltf_export import decode_glb
from extrude.model import InsertRef, Provenance

SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10)]


def rules(*layers: dict[str, Any], **extra: Any) -> Config:
    return parse_config({"layers": [*layers, {"match": "*", "kind": "ignore"}], **extra})


BUILDINGS = {"match": "E", "kind": "building", "height": 9}


def project(units: int = 6, factor: float = 1.0, offset: tuple[float, float] = (0, 0)) -> Drawing:
    """Dos edificios (el norte con patio), un vial y diez INSERT de un mismo bloque."""
    doc = ezdxf.new()
    doc.units = units

    def pts(points: list[tuple[float, float]], dx: float = 0, dy: float = 0) -> list[Any]:
        return [((x + dx + offset[0]) * factor, (y + dy + offset[1]) * factor) for x, y in points]

    msp = doc.modelspace()
    msp.add_lwpolyline(pts(SQUARE), close=True, dxfattribs={"layer": "E"})
    patio = msp.add_hatch(dxfattribs={"layer": "E"})
    patio.paths.add_polyline_path(
        pts([(0, 0), (30, 0), (30, 20), (0, 20)], dy=40), is_closed=True, flags=1
    )
    patio.paths.add_polyline_path(pts([(10, 5), (20, 5), (20, 15), (10, 15)], dy=40), flags=0)
    msp.add_lwpolyline(
        pts([(-5, -10), (60, -10), (60, -4), (-5, -4)]), close=True, dxfattribs={"layer": "V"}
    )
    block = doc.blocks.new("CASETA")
    block.add_lwpolyline(
        [(0, 0), (2 * factor, 0), (2 * factor, 2 * factor), (0, 2 * factor)], close=True
    )
    for i in range(10):
        insert = pts([(40 + 4 * (i % 5), 20 + 4 * (i // 5))])[0]
        msp.add_blockref("CASETA", insert, dxfattribs={"layer": "C"})
    return doc


PROJECT_RULES = (
    {"match": "E", "kind": "building", "height": 9, "detect_holes": True},
    {"match": "V", "kind": "road"},
    {"match": "C", "kind": "building", "height": 3},
)


def run(tmp_path: Path, doc: Drawing, name: str, config: Config | None = None) -> dict[str, Any]:
    source = tmp_path / f"{name}.dxf"
    doc.saveas(source)
    out = tmp_path / name
    result = convert(source, ConvertOptions(config or rules(*PROJECT_RULES), out))
    catalog = json.loads((out / CATALOG_NAME).read_text(encoding="utf-8"))
    assert catalog == result.catalog
    return catalog


def by_layer(catalog: dict[str, Any], layer: str) -> list[dict[str, Any]]:
    return [o for o in catalog["objects"] if o["layer"] == layer]


def test_same_input_produces_same_ids_and_revision(tmp_path: Path) -> None:
    first = run(tmp_path, project(), "a")
    second = run(tmp_path, project(), "b")
    assert first["geometryRevision"].startswith("sha256:")
    assert first == second  # RF-01: mismo archivo y configuración → mismo catálogo


def test_ids_are_unique_and_independent_of_height(tmp_path: Path) -> None:
    base = run(tmp_path, project(), "a")
    taller = run(
        tmp_path,
        project(),
        "b",
        rules({**PROJECT_RULES[0], "height": 12}, *PROJECT_RULES[1:]),
    )
    ids = [o["id"] for o in base["objects"]]
    assert len(ids) == len(set(ids)) == 2 + 1 + 10
    # La forma cambia (otra revisión) pero la identidad de cada objeto es la misma
    assert taller["geometryRevision"] != base["geometryRevision"]
    assert [o["id"] for o in taller["objects"]] == ids


def test_labels_run_north_to_south_per_kind(tmp_path: Path) -> None:
    catalog = run(tmp_path, project(), "a")
    buildings = by_layer(catalog, "E")
    north = max(buildings, key=lambda o: o["centerM"][1])
    assert north["label"] == "E-001"  # el patio está más al norte (+Y del plano)
    assert by_layer(catalog, "V")[0]["label"] == "V-001"
    assert catalog["north"]["axis"] == "+Y"
    labels = [o["label"] for o in catalog["objects"]]
    assert len(labels) == len(set(labels))


def test_units_and_scale_do_not_change_identity_or_revision(tmp_path: Path) -> None:
    meters = run(tmp_path, project(), "m")
    millimeters = run(tmp_path, project(units=4, factor=1000), "mm")
    assert millimeters["sourceUnits"] == "mm"
    assert millimeters["geometryRevision"] == meters["geometryRevision"]
    assert [o["id"] for o in millimeters["objects"]] == [o["id"] for o in meters["objects"]]


def test_utm_offset_keeps_ids_and_reports_drawing_coordinates(tmp_path: Path) -> None:
    local = run(tmp_path, project(), "local")
    utm = run(tmp_path, project(offset=(230_000, 4_140_000)), "utm")
    assert [o["id"] for o in utm["objects"]] == [o["id"] for o in local["objects"]]
    assert utm["geometryRevision"] != local["geometryRevision"]  # otra posición: otra forma
    patio = by_layer(utm, "E")[0]
    assert patio["centerM"][0] > 230_000  # antes del recentrado: referencias espaciales reales
    assert utm["originOffset"][0] > 230_000


def test_insert_copies_get_distinct_ids_and_instance_refs(tmp_path: Path) -> None:
    catalog = run(tmp_path, project(), "a")
    copies = by_layer(catalog, "C")
    assert len(copies) == 10
    assert len({o["id"] for o in copies}) == 10
    assert {o["provenance"]["insert"]["block"] for o in copies} == {"CASETA"}
    refs = sorted(o["export"]["instance"] for o in copies)
    assert refs == list(range(10))
    assert {o["export"]["node"] for o in copies} == {"C/CASETA"}

    doc, _ = decode_glb((tmp_path / "a" / "model.glb").read_bytes())
    node = next(n for n in doc["nodes"] if n.get("name") == "C/CASETA")
    glb_ids = [ids[0] for ids in node["extras"]["object_ids"]]
    by_id = {o["id"]: o["export"]["instance"] for o in copies}
    assert [by_id[i] for i in glb_ids] == list(range(10))
    assert doc["scenes"][0]["extras"]["geometry_revision"] == catalog["geometryRevision"]


def test_merged_objects_carry_id_and_label_in_glb(tmp_path: Path) -> None:
    catalog = run(tmp_path, project(), "a")
    doc, _ = decode_glb((tmp_path / "a" / "model.glb").read_bytes())
    node = next(n for n in doc["nodes"] if n.get("name") == "E")
    table = node["extras"]["objects"]
    for entry in by_layer(catalog, "E"):
        index = entry["export"]["objectIndex"]
        assert table["id"][index] == entry["id"]
        assert table["label"][index] == entry["label"]


def test_tiled_export_keeps_ids_without_node_refs(tmp_path: Path) -> None:
    source = tmp_path / "t.dxf"
    project().saveas(source)
    result = convert(source, ConvertOptions(rules(*PROJECT_RULES), tmp_path / "t", tile_size=20))
    assert all(o["export"] is None for o in result.catalog["objects"])
    manifest = json.loads((tmp_path / "t" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["geometry_revision"] == result.geometry_revision
    ids = set()
    for tile in (tmp_path / "t" / "tiles").glob("*.glb"):
        doc, _ = decode_glb(tile.read_bytes())
        for node in doc["nodes"]:
            extras = node.get("extras", {})
            ids.update(extras.get("objects", {}).get("id", []))
            ids.update(i for group in extras.get("object_ids", []) for i in group)
    assert ids == {o["id"] for o in result.catalog["objects"]}


def test_surfaces_are_labeled_at_construction(tmp_path: Path) -> None:
    catalog = run(tmp_path, project(), "a")
    patio, plain = sorted(by_layer(catalog, "E"), key=lambda o: o["label"])
    assert set(patio["surfaces"]) == {"roof", "facade_exterior", "facade_courtyard"}
    assert set(plain["surfaces"]) == {"roof", "facade_exterior"}
    # Fachada exterior = perímetro × altura; patio = perímetro del hueco × altura
    assert patio["surfaces"]["facade_exterior"]["areaM2"] == pytest.approx(100 * 9)
    assert patio["surfaces"]["facade_courtyard"]["areaM2"] == pytest.approx(40 * 9)
    assert patio["surfaces"]["roof"]["areaM2"] == pytest.approx(30 * 20 - 10 * 10)
    road = by_layer(catalog, "V")[0]
    assert set(road["surfaces"]) == {"ground"}


def test_thick_roads_and_water_are_a_single_ground_surface(tmp_path: Path) -> None:
    config = rules(
        {"match": "V", "kind": "road", "height": 0.2},
        {"match": "C", "kind": "water", "height": 0.5, "bottom_cap": True},
    )
    catalog = run(tmp_path, project(), "a", config)
    assert set(by_layer(catalog, "V")[0]["surfaces"]) == {"ground"}
    assert {s for o in by_layer(catalog, "C") for s in o["surfaces"]} == {"ground", "underside"}


def test_shared_source_is_disambiguated_by_position(tmp_path: Path) -> None:
    """Un HATCH con dos islas da dos objetos con la misma procedencia."""
    doc = ezdxf.new()
    doc.units = 6
    hatch = doc.modelspace().add_hatch(dxfattribs={"layer": "E"})
    hatch.paths.add_polyline_path(SQUARE, is_closed=True, flags=1)
    hatch.paths.add_polyline_path([(x + 50, y) for x, y in SQUARE], is_closed=True, flags=1)
    path = tmp_path / "islands.dxf"
    doc.saveas(path)
    first = build_scene(read_dxf(path), rules(BUILDINGS))
    second = build_scene(read_dxf(path), rules(BUILDINGS))
    ids = [o.object_id for o in first.layers["E"].objects]
    assert len(set(ids)) == 2
    assert {i for i in ids if i} == {o.object_id for o in second.layers["E"].objects}
    base = min(ids, key=len)
    assert base is not None and f"{base}-2" in ids


def test_virtual_entities_from_nested_blocks_get_ids(tmp_path: Path) -> None:
    doc = ezdxf.new()
    doc.units = 6
    inner = doc.blocks.new("INNER")
    inner.add_lwpolyline(SQUARE, close=True)
    outer = doc.blocks.new("OUTER")
    outer.add_blockref("INNER", (0, 0))
    outer.add_blockref("INNER", (20, 0))
    doc.modelspace().add_blockref("OUTER", (0, 0), dxfattribs={"layer": "E"})
    path = tmp_path / "nested.dxf"
    doc.saveas(path)
    objects = build_scene(read_dxf(path), rules(BUILDINGS)).layers["E"].objects
    assert len(objects) == 2
    assert all(o.object_id for o in objects)
    assert len({o.object_id for o in objects}) == 2


def test_source_key_distinguishes_modelspace_and_block_entities() -> None:
    insert = InsertRef("2A#0", "B", tuple(np.eye(4).reshape(-1)))
    assert source_key(Provenance("LWPOLYLINE", "1F")) == "h:1F"
    nested = source_key(Provenance("LWPOLYLINE", None, ("B",), insert=insert))
    assert nested == "i:2A#0:B:LWPOLYLINE"
    copy = source_key(Provenance("LWPOLYLINE", None, ("B",), insert=replace_handle(insert)))
    assert copy != nested


def replace_handle(insert: InsertRef) -> InsertRef:
    return InsertRef("2A#1", insert.block, insert.matrix)


def test_report_and_server_files_include_catalog(tmp_path: Path) -> None:
    run(tmp_path, project(), "a")
    report = json.loads((tmp_path / "a" / "report.json").read_text(encoding="utf-8"))
    assert report["geometry_revision"].startswith("sha256:")
    assert report["catalog"] == {"file": "objects.json", "objects": 13}
