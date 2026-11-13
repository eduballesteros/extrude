"""Fase 1: GLB base + materials.json → GLB materializado (CA-01, CA-05, CA-06, RG-03…RG-10)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import ezdxf
import numpy as np
import pytest
from ezdxf.document import Drawing
from typer.testing import CliRunner

from extrude.cli import app
from extrude.config import Config, parse_config
from extrude.convert import ConvertOptions, convert
from extrude.errors import ExtrudeError
from extrude.gltf_export import INSTANCING_EXT
from extrude.material_library import LIBRARY_VERSION
from extrude.materiality import parse_plan
from extrude.materialize import GlbData, materialize
from extrude.node_tools import tools_dir, validate
from extrude.surfaces import Surface

node_tools = pytest.mark.skipif(
    shutil.which("node") is None or not (tools_dir() / "node_modules" / "gltf-validator").is_dir(),
    reason="Node o tools/node_modules no disponibles (npm install en tools/)",
)

HEIGHT = 9.0


def rules(**extra: Any) -> Config:
    return parse_config(
        {
            "layers": [
                {"match": "E", "kind": "building", "height": HEIGHT, "detect_holes": True},
                {"match": "C", "kind": "building", "height": 3},
                {"match": "*", "kind": "ignore"},
            ],
            **extra,
        }
    )


def drawing(units: int = 6, factor: float = 1.0, copy_scales: tuple[float, ...] = ()) -> Drawing:
    """Edificio de 10 × 10 m, otro con patio, y copias de un bloque de 2 × 2 m."""
    doc = ezdxf.new()
    doc.units = units
    msp = doc.modelspace()

    def pts(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        return [(x * factor, y * factor) for x, y in points]

    msp.add_lwpolyline(
        pts([(0, 0), (10, 0), (10, 10), (0, 10)]), close=True, dxfattribs={"layer": "E"}
    )
    patio = msp.add_hatch(dxfattribs={"layer": "E"})
    patio.paths.add_polyline_path(pts([(0, 40), (30, 40), (30, 60), (0, 60)]), flags=1)
    patio.paths.add_polyline_path(pts([(10, 45), (20, 45), (20, 55), (10, 55)]), flags=0)
    block = doc.blocks.new("CASETA")
    block.add_lwpolyline(pts([(0, 0), (2, 0), (2, 2), (0, 2)]), close=True)
    for i, scale in enumerate(copy_scales):
        msp.add_blockref(
            "CASETA",
            ((40 + 6 * i) * factor, 0),
            dxfattribs={"layer": "C", "xscale": scale, "yscale": scale},
        )
    return doc


def build(tmp_path: Path, name: str, doc: Drawing, config: Config | None = None) -> Path:
    source = tmp_path / f"{name}.dxf"
    doc.saveas(source)
    convert(source, ConvertOptions(config or rules(), tmp_path / name))
    return tmp_path / name


def catalog(out: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((out / "objects.json").read_text(encoding="utf-8"))
    return data


def plan_for(out: Path, *assignments: dict[str, Any]) -> Any:
    return parse_plan(
        json.dumps(
            {
                "schemaVersion": 1,
                "projectId": "test",
                "geometryRevision": catalog(out)["geometryRevision"],
                "materialRevision": "mat-001",
                "catalogVersion": LIBRARY_VERSION,
                "assignments": list(assignments),
            }
        )
    )


def assign(ids: list[str], surface: str, material: str, **parameters: Any) -> dict[str, Any]:
    return {
        "objectIds": ids,
        "surface": surface,
        "materialId": material,
        "materialVersion": "1",
        "parameters": parameters,
        "origin": "manual",
    }


def objects(out: Path, layer: str) -> list[dict[str, Any]]:
    return sorted(
        (o for o in catalog(out)["objects"] if o["layer"] == layer), key=lambda o: o["label"]
    )


def primitives(data: bytes) -> list[dict[str, Any]]:
    """Primitivas de triángulos con sus atributos decodificados y el nombre de su material."""
    glb = GlbData(data)
    doc = glb.doc
    out = []
    for node in doc["nodes"]:
        if "mesh" not in node:
            continue
        for prim in doc["meshes"][node["mesh"]]["primitives"]:
            if "_SURFACE" not in prim["attributes"]:
                continue
            attrs = {k: glb.array(v) for k, v in prim["attributes"].items()}
            faces = glb.array(prim["indices"]).astype(np.int64).reshape(-1, 3)
            material = doc["materials"][prim["material"]]
            out.append(
                {
                    "node": node,
                    "attrs": attrs,
                    "faces": faces,
                    "material": material["name"],
                    "material_def": material,
                }
            )
    return out


def triangles(prims: list[dict[str, Any]], node_name: str) -> list[tuple[float, ...]]:
    """Triángulos (posiciones redondeadas) de un nodo, ordenados: geometría independiente del
    reparto de vértices entre primitivas."""
    out = []
    for p in prims:
        if p["node"].get("name") != node_name:
            continue
        pos = p["attrs"]["POSITION"].astype(np.float64)
        for face in p["faces"]:
            corners = sorted(tuple(np.round(pos[i], 5)) for i in face)
            out.append(tuple(c for corner in corners for c in corner))
    return sorted(out)


def test_facades_and_roofs_get_their_own_materials(tmp_path: Path) -> None:
    """CA-01 y RG-10: dos superficies distintas, misma geometría (patio y altura incluidos)."""
    out = build(tmp_path, "a", drawing())
    ids = [o["id"] for o in objects(out, "E")]
    plan = plan_for(
        out,
        assign(ids, "facade_exterior", "brick_light"),
        assign(ids, "roof", "roof_gravel"),
    )
    base = (out / "model.glb").read_bytes()
    data, stats = materialize(base, plan)
    prims = primitives(data)
    by_material = {p["material"]: p for p in prims if p["node"]["name"] == "E"}
    assert set(by_material) == {"Ladrillo claro", "Grava de cubierta", "E"}
    surfaces = {
        name: set(np.rint(p["attrs"]["_SURFACE"]).astype(int).tolist())
        for name, p in by_material.items()
    }
    assert surfaces["Ladrillo claro"] == {Surface.FACADE_EXTERIOR}
    assert surfaces["Grava de cubierta"] == {Surface.ROOF}
    assert surfaces["E"] == {Surface.FACADE_COURTYARD}  # sin asignación: material de la capa
    assert triangles(prims, "E") == triangles(primitives(base), "E")
    assert stats.textures == 2
    assert stats.assigned_triangles > 0


def test_textures_are_embedded_and_output_is_deterministic(tmp_path: Path) -> None:
    """RF-16/CA-10: ni URIs ni recursos externos; mismo plan → mismos bytes."""
    out = build(tmp_path, "a", drawing())
    ids = [o["id"] for o in objects(out, "E")]
    plan = plan_for(out, assign(ids, "facade_exterior", "brick_light"))
    base = (out / "model.glb").read_bytes()
    first, _ = materialize(base, plan)
    second, _ = materialize(base, plan)
    assert first == second
    doc = GlbData(first).doc
    assert all("uri" not in image and "bufferView" in image for image in doc["images"])
    assert all("uri" not in buffer for buffer in doc["buffers"])
    png = GlbData(first)
    view = doc["bufferViews"][doc["images"][0]["bufferView"]]
    start = view["byteOffset"]
    assert png.blob[start : start + 8] == b"\x89PNG\r\n\x1a\n"
    extras = doc["scenes"][0]["extras"]
    assert extras["material_revision"] == "mat-001"
    assert extras["geometry_revision"] == catalog(out)["geometryRevision"]


def wall_uv_span(data: bytes, material: str) -> tuple[float, float]:
    prim = next(p for p in primitives(data) if p["material"] == material)
    uv = prim["attrs"]["TEXCOORD_0"]
    return float(np.ptp(uv[:, 0])), float(np.ptp(uv[:, 1]))


@pytest.mark.parametrize(("units", "factor"), [(6, 1.0), (4, 1000.0)])
def test_texture_repeats_every_tile_size_in_meters(
    tmp_path: Path, units: int, factor: float
) -> None:
    """RF-11/CA-06: ladrillo de 2 m → el muro de 10 m × 9 m recorre 20 m de contorno… en
    unidades de textura: contorno / 2 y altura / 2, igual en m que en mm."""
    out = build(tmp_path, "a", drawing(units, factor))
    small = objects(out, "E")[1]  # E-002: el edificio de 10 × 10 al sur
    assert small["footprintAreaM2"] == pytest.approx(100)
    data, _ = materialize(
        (out / "model.glb").read_bytes(),
        plan_for(out, assign([small["id"]], "facade_exterior", "brick_light")),
    )
    u, v = wall_uv_span(data, "Ladrillo claro")
    assert u == pytest.approx(40 / 2)  # perímetro 40 m
    assert v == pytest.approx(HEIGHT / 2)
    # Con otro tamaño de pieza la repetición cambia en proporción
    data, _ = materialize(
        (out / "model.glb").read_bytes(),
        plan_for(out, assign([small["id"]], "facade_exterior", "brick_light", tileSizeM=[4, 3])),
    )
    assert wall_uv_span(data, "Ladrillo claro") == pytest.approx((10, 3))


def test_wall_uvs_are_continuous_around_corners(tmp_path: Path) -> None:
    """RG-03: u avanza sin saltos de un paño al siguiente; v = cota en la esquina."""
    out = build(tmp_path, "a", drawing())
    small = objects(out, "E")[1]
    data, _ = materialize(
        (out / "model.glb").read_bytes(),
        plan_for(out, assign([small["id"]], "facade_exterior", "brick_light")),
    )
    prim = next(p for p in primitives(data) if p["material"] == "Ladrillo claro")
    pos = prim["attrs"]["POSITION"].astype(np.float64)
    uv = prim["attrs"]["TEXCOORD_0"].astype(np.float64)
    # En cada esquina coinciden vértices de dos paños: misma u (salvo el cierre del anillo)
    by_corner: dict[tuple[float, ...], set[float]] = {}
    for p, (u, v) in zip(np.round(pos, 4), uv, strict=True):
        by_corner.setdefault(tuple(p), set()).add(round(u, 4))
        assert -v * 2 == pytest.approx(p[1], abs=1e-4)  # glTF Y = cota; v invertida
    seams = [c for c in by_corner.values() if len(c) > 1]
    assert len(seams) == 2  # solo la arista de cierre (arriba y abajo): u = 0 y u = 20
    assert all(s == {0.0, 20.0} for s in seams)


def test_rotation_parameter_turns_the_texture(tmp_path: Path) -> None:
    out = build(tmp_path, "a", drawing())
    small = objects(out, "E")[1]
    data, _ = materialize(
        (out / "model.glb").read_bytes(),
        plan_for(out, assign([small["id"]], "facade_exterior", "brick_light", rotationDeg=90)),
    )
    u, v = wall_uv_span(data, "Ladrillo claro")
    assert (u, v) == pytest.approx((HEIGHT / 2, 40 / 2))


def instanced_nodes(data: bytes) -> list[dict[str, Any]]:
    return [n for n in GlbData(data).doc["nodes"] if INSTANCING_EXT in n.get("extensions", {})]


def test_changing_one_insert_copy_keeps_the_others(tmp_path: Path) -> None:
    """CA-05/RG-08: diez INSERT iguales; solo cambia la copia elegida, también al exportar."""
    out = build(tmp_path, "a", drawing(copy_scales=(1.0,) * 10))
    copies = objects(out, "C")
    assert len(copies) == 10 and all(c["export"]["node"] == "C/CASETA" for c in copies)
    chosen = copies[3]["id"]
    data, stats = materialize(
        (out / "model.glb").read_bytes(),
        plan_for(out, assign([chosen], "facade_exterior", "metal_dark")),
    )
    nodes = instanced_nodes(data)
    assert stats.split_instance_nodes == 1
    assert sorted(n["extras"]["instances"] for n in nodes) == [1, 9]
    single = next(n for n in nodes if n["extras"]["instances"] == 1)
    assert single["extras"]["object_ids"] == [[chosen]]
    doc = GlbData(data).doc
    names = {
        doc["materials"][p["material"]]["name"]
        for n in nodes
        for p in doc["meshes"][n["mesh"]]["primitives"]
    }
    assert names == {"Metal oscuro", "C"}
    rest = next(n for n in nodes if n["extras"]["instances"] == 9)
    rest_materials = {
        doc["materials"][p["material"]]["name"] for p in doc["meshes"][rest["mesh"]]["primitives"]
    }
    assert rest_materials == {"C"}
    # Las dos agrupaciones juntas siguen siendo las diez copias, sin duplicar ninguna
    all_ids = [i for n in nodes for ids in n["extras"]["object_ids"] for i in ids]
    assert sorted(all_ids) == sorted(c["id"] for c in copies)


def test_scaled_insert_does_not_enlarge_bricks(tmp_path: Path) -> None:
    """RG-06: la copia al doble de tamaño tiene el doble de ladrillos, no ladrillos dobles."""
    out = build(tmp_path, "a", drawing(copy_scales=(1.0, 2.0)), rules(instancing_min=2))
    copies = objects(out, "C")
    assert {c["export"]["node"] for c in copies} == {"C/CASETA"}  # siguen instanciadas
    data, _ = materialize(
        (out / "model.glb").read_bytes(),
        plan_for(out, assign([c["id"] for c in copies], "facade_exterior", "brick_light")),
    )
    nodes = instanced_nodes(data)
    assert len(nodes) == 2  # separadas por escala: cada una con su UV física
    glb = GlbData(data)
    spans = {}
    for node in nodes:
        scale = glb.array(node["extensions"][INSTANCING_EXT]["attributes"]["SCALE"])[0]
        prim = next(
            p
            for p in glb.doc["meshes"][node["mesh"]]["primitives"]
            if glb.doc["materials"][p["material"]]["name"] == "Ladrillo claro"
        )
        uv = glb.array(prim["attributes"]["TEXCOORD_0"])
        spans[round(float(scale[0]), 3)] = float(np.ptp(uv[:, 0]))
    # Perímetro real 8 m y 16 m, ladrillo de 2 m
    assert spans == pytest.approx({1.0: 4.0, 2.0: 8.0})


def test_invalid_plans_and_inputs_are_rejected(tmp_path: Path) -> None:
    out = build(tmp_path, "a", drawing())
    base = (out / "model.glb").read_bytes()
    small = objects(out, "E")[1]
    stale = plan_for(out, assign([small["id"]], "facade_exterior", "brick_light"))
    stale = stale.model_copy(update={"geometry_revision": "sha256:" + "0" * 64})
    with pytest.raises(ExtrudeError, match="revisión geométrica"):
        materialize(base, stale)
    no_courtyard = plan_for(out, assign([small["id"]], "facade_courtyard", "render_white"))
    with pytest.raises(ExtrudeError, match="facade_courtyard"):
        materialize(base, no_courtyard)
    good = plan_for(out, assign([small["id"]], "facade_exterior", "brick_light"))
    data, _ = materialize(base, good)
    with pytest.raises(ExtrudeError, match="ya está materializado"):
        materialize(data, good)


def test_cli_materializes_without_reconverting(tmp_path: Path) -> None:
    out = build(tmp_path, "a", drawing())
    ids = [o["id"] for o in objects(out, "E")]
    plan = out / "materials.json"
    plan.write_text(
        plan_for(out, assign(ids, "facade_exterior", "brick_light")).to_json(), encoding="utf-8"
    )
    result = CliRunner().invoke(app, ["materialize", str(out / "model.glb"), str(plan)])
    assert result.exit_code == 0, result.output
    target = out / "model.materialized.glb"
    assert target.is_file()
    assert not list(out.glob("*.partial"))

    # Plan erróneo: no se escribe nada ni se toca el GLB anterior
    bad = out / "bad.json"
    bad.write_text(
        plan_for(out, assign(ids, "foundation", "brick_light")).to_json(), encoding="utf-8"
    )
    before = target.read_bytes()
    result = CliRunner().invoke(app, ["materialize", str(out / "model.glb"), str(bad)])
    assert result.exit_code == 2
    assert target.read_bytes() == before

    result = CliRunner().invoke(app, ["library"])
    assert result.exit_code == 0 and "brick_light" in result.output


@node_tools
def test_materialized_glb_passes_validator(tmp_path: Path) -> None:
    out = build(tmp_path, "a", drawing(copy_scales=(1.0,) * 10))
    buildings = [o["id"] for o in objects(out, "E")]
    copies = [o["id"] for o in objects(out, "C")]
    data, _ = materialize(
        (out / "model.glb").read_bytes(),
        plan_for(
            out,
            assign(buildings, "facade_exterior", "brick_red", rotationDeg=15),
            assign(buildings, "roof", "roof_gravel"),
            assign(buildings[:1], "facade_courtyard", "glass_blue"),
            assign(copies[:2], "roof", "wood_exterior", color="#aa7744"),
        ),
    )
    path = tmp_path / "materialized.glb"
    path.write_bytes(data)
    (report,) = validate([path])
    assert report["errors"] == 0, report["messages"]
    assert report["warnings"] == 0, report["messages"]


def test_color_compensation_keeps_the_hue(tmp_path: Path) -> None:
    """La compensación de la textura escala los tres canales por igual: el ladrillo claro sigue
    siendo beige (R > G > B, mismas proporciones), no blanco saturado."""
    from extrude.gltf_export import srgb_to_linear

    out = build(tmp_path, "a", drawing())
    ids = [o["id"] for o in objects(out, "E")]
    data, _ = materialize(
        (out / "model.glb").read_bytes(),
        plan_for(out, assign(ids, "facade_exterior", "brick_light")),
    )
    material = next(m for m in GlbData(data).doc["materials"] if m["name"] == "Ladrillo claro")
    factor = material["pbrMetallicRoughness"]["baseColorFactor"][:3]
    expected = srgb_to_linear("#d8c3a5")
    assert max(factor) <= 1
    assert np.allclose(np.array(factor) / factor[0], np.array(expected) / expected[0])
