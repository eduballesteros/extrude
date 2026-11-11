"""Aceptación de la Fase 3: GLB válido, recentrado, Y-up, instancing, teselas e informe."""

from __future__ import annotations

import json
import shutil
import struct
from pathlib import Path
from typing import Any

import ezdxf
import numpy as np
import pytest
import sample_dxf as s
from typer.testing import CliRunner

from extrude.cli import USER_ERROR_EXIT, app
from extrude.config import load_config, parse_config
from extrude.convert import ConvertOptions, ConvertResult, convert
from extrude.dxf_reader import read_dxf
from extrude.gltf_export import decode_glb, srgb_to_linear
from extrude.node_tools import Compression, attributes, tools_dir, validate

CONFIGS = Path(__file__).parents[2] / "samples" / "config"
COMPONENTS = {5126: (np.float32, 4), 5123: (np.uint16, 2), 5125: (np.uint32, 4)}
WIDTH = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}
FLOAT32_AT_UTM_M = 0.25  # resolución de float32 a 4 140 000 m: el jitter que evitamos

node_tools = pytest.mark.skipif(
    shutil.which("node") is None or not (tools_dir() / "node_modules" / "gltf-validator").is_dir(),
    reason="Node o tools/node_modules no disponibles (npm install en tools/)",
)


def run(samples_dir: Path, tmp_path: Path, name: str, **kwargs: Any) -> ConvertResult:
    config = load_config(CONFIGS / f"{name}.yaml")
    return convert(samples_dir / f"{name}.dxf", ConvertOptions(config, tmp_path / name, **kwargs))


class Glb:
    def __init__(self, path: Path) -> None:
        self.doc, self.blob = decode_glb(path.read_bytes())

    def accessor(self, index: int) -> np.ndarray:
        acc = self.doc["accessors"][index]
        view = self.doc["bufferViews"][acc["bufferView"]]
        dtype, _ = COMPONENTS[acc["componentType"]]
        width = WIDTH[acc["type"]]
        data = np.frombuffer(
            self.blob, dtype=dtype, count=acc["count"] * width, offset=view["byteOffset"]
        )
        return data.reshape(acc["count"], width) if width > 1 else data

    def node(self, name: str) -> dict[str, Any]:
        return next(n for n in self.doc["nodes"] if n.get("name") == name)

    def primitive(self, name: str, mode: int = 4) -> dict[str, Any]:
        mesh = self.doc["meshes"][self.node(name)["mesh"]]
        return next(p for p in mesh["primitives"] if p["mode"] == mode)

    @property
    def extras(self) -> dict[str, Any]:
        extras: dict[str, Any] = self.doc["scenes"][0]["extras"]
        return extras


@pytest.fixture(scope="module")
def utm(samples_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[ConvertResult, Glb]:
    config = load_config(CONFIGS / "block_2d.yaml")
    out = tmp_path_factory.mktemp("utm")
    result = convert(samples_dir / "block_2d_utm.dxf", ConvertOptions(config, out))
    return result, Glb(out / "model.glb")


def test_glb_header_and_structure(utm: tuple[ConvertResult, Glb]) -> None:
    _, glb = utm
    assert glb.doc["asset"]["version"] == "2.0"
    names = {n["name"] for n in glb.doc["nodes"]}
    assert names == {"AGUA", "EDIFICIOS", "VIALES"}
    edif = glb.node("EDIFICIOS")["extras"]
    assert edif["kind"] == "building"
    assert edif["source_entities"] == {"LWPOLYLINE": 5}
    prim = glb.primitive("EDIFICIOS")
    assert edif["triangle_count"] == len(glb.accessor(prim["indices"])) // 3


def test_recentering_removes_utm_jitter(utm: tuple[ConvertResult, Glb]) -> None:
    result, glb = utm
    origin = np.array(glb.extras["origin_offset"])
    assert origin[:2] == pytest.approx([s.UTM_OFFSET[0] + 32.5, s.UTM_OFFSET[1] + 15.0])
    positions = glb.accessor(glb.primitive("EDIFICIOS")["attributes"]["POSITION"])
    assert positions.dtype == np.float32
    assert np.abs(positions).max() < 100  # coordenadas locales pequeñas

    # GLB (Y-up, float32) → DWG absoluto en float64 y comparación con la geometría original
    restored = np.column_stack(
        [positions[:, 0] + origin[0], -positions[:, 2] + origin[1], positions[:, 1] + origin[2]]
    )
    original = np.vstack([o.mesh.vertices for o in result.build.layers["EDIFICIOS"].objects])
    error = np.abs(restored - original).max()
    assert error < 1e-4  # < 0,1 mm
    assert error < FLOAT32_AT_UTM_M / 1000


SAMPLE_CENTER_WGS84 = (37.367608861, -6.048517018)  # pyproj: EPSG:25830 (230032.5, 4140015.0)


@pytest.mark.parametrize("recenter", [True, False])
def test_utm_crs_gives_geographic_center(samples_dir: Path, tmp_path: Path, recenter: bool) -> None:
    config = load_config(CONFIGS / "block_2d.yaml").model_copy(
        update={"crs": "EPSG:25830", "recenter": recenter}
    )
    convert(samples_dir / "block_2d_utm.dxf", ConvertOptions(config, tmp_path, tile_size=40))
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    tile = Glb(next((tmp_path / "tiles").glob("*.glb")))
    for extras in (tile.extras, manifest):
        assert extras["center_lat"] == pytest.approx(SAMPLE_CENTER_WGS84[0], abs=1e-8)
        assert extras["center_lon"] == pytest.approx(SAMPLE_CENTER_WGS84[1], abs=1e-8)


def test_without_crs_there_is_no_geographic_center(utm: tuple[ConvertResult, Glb]) -> None:
    _, glb = utm
    assert glb.extras["crs"] is None
    assert glb.extras["center_lat"] is None
    assert glb.extras["center_lon"] is None


def test_rotated_instance_bounds_match_the_original_drawing(tmp_path: Path) -> None:
    doc = ezdxf.new()
    doc.units = 6
    block = doc.blocks.new("SQUARE")
    block.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True)
    doc.modelspace().add_blockref("SQUARE", (0, 0), dxfattribs={"layer": "E", "rotation": 45})
    doc.modelspace().add_blockref("SQUARE", (100, 0), dxfattribs={"layer": "E"})
    source = tmp_path / "rotated.dxf"
    doc.saveas(source)
    bbox = read_dxf(source).bbox
    assert bbox is not None
    config = parse_config(
        {"instancing_min": 2, "layers": [{"match": "E", "kind": "building", "height": 3}]}
    )
    result = convert(source, ConvertOptions(config, tmp_path / "out"))
    assert result.outputs[0].stats.instances == 2
    expected = (np.asarray(bbox.min) + np.asarray(bbox.max)) / 2
    assert result.origin[:2] == pytest.approx(expected[:2])


def test_invalid_geolocation_does_not_prevent_export(tmp_path: Path) -> None:
    doc = ezdxf.new()
    doc.units = 6
    doc.modelspace().add_lwpolyline(
        [(1e12, 0), (1e12 + 10, 0), (1e12 + 10, 10), (1e12, 10)],
        close=True,
        dxfattribs={"layer": "E"},
    )
    source = tmp_path / "extreme.dxf"
    doc.saveas(source)
    config = parse_config(
        {"crs": "EPSG:25830", "layers": [{"match": "E", "kind": "building", "height": 3}]}
    )
    result = convert(source, ConvertOptions(config, tmp_path / "out"))
    assert Glb(result.outputs[0].path).extras["center_lat"] is None
    assert any("geolocalizar" in w for w in result.report["warnings"])


@pytest.mark.parametrize("size", [float("nan"), float("inf"), -float("inf"), 0, -1])
def test_convert_rejects_nonfinite_or_nonpositive_tiles(tmp_path: Path, size: float) -> None:
    from extrude.errors import ExtrudeError

    with pytest.raises(ExtrudeError, match="finito"):
        convert(tmp_path / "not-read.dxf", ConvertOptions(parse_config({}), tmp_path, size))


def test_y_up_axis_and_heights(utm: tuple[ConvertResult, Glb]) -> None:
    _, glb = utm
    prim = glb.primitive("EDIFICIOS")
    positions = glb.accessor(prim["attributes"]["POSITION"])
    assert positions[:, 1].min() == pytest.approx(0)
    assert positions[:, 1].max() == pytest.approx(max(b.floors for b in s.BUILDINGS) * 3)
    assert glb.extras["axis_transform"] == "(x, y, z)_dwg -> (x, z, -y)_gltf"
    normals = glb.accessor(prim["attributes"]["NORMAL"])
    assert np.linalg.norm(normals, axis=1) == pytest.approx(1, abs=1e-5)
    assert normals[:, 1].max() == pytest.approx(1)  # tapas hacia +Y


def test_object_ids_and_metadata(utm: tuple[ConvertResult, Glb]) -> None:
    _, glb = utm
    prim = glb.primitive("EDIFICIOS")
    ids = glb.accessor(prim["attributes"]["_OBJECT_ID"])
    assert len(ids) == len(glb.accessor(prim["attributes"]["POSITION"]))
    assert sorted(set(ids.astype(int))) == [0, 1, 2, 3]
    table = glb.node("EDIFICIOS")["extras"]["objects"]
    assert table["height"] == [b.floors * 3.0 for b in s.BUILDINGS]
    assert table["floors"] == [b.floors for b in s.BUILDINGS]
    assert table["base"] == [0.0] * len(s.BUILDINGS)
    assert set(table["height_origin"]) == {"text"}
    assert all(table["handle"])


def test_materials_are_linear(utm: tuple[ConvertResult, Glb]) -> None:
    _, glb = utm
    water = next(m for m in glb.doc["materials"] if m["name"] == "AGUA")
    assert water["pbrMetallicRoughness"]["baseColorFactor"][:3] == pytest.approx(
        srgb_to_linear("#4a7fa8")
    )
    assert srgb_to_linear("#ffffff") == pytest.approx([1, 1, 1])
    assert srgb_to_linear("#000000") == [0, 0, 0]


def test_transparent_material_blends(samples_dir: Path, tmp_path: Path) -> None:
    run(samples_dir, tmp_path, "dirty")
    glb = Glb(tmp_path / "dirty" / "model.glb")
    water = next(m for m in glb.doc["materials"] if m["name"] == "AGUA")
    assert water["alphaMode"] == "BLEND"
    assert water["pbrMetallicRoughness"]["baseColorFactor"][3] == 0.8


class TestInstancing:
    @pytest.fixture(scope="class")
    @classmethod
    def glb(cls, samples_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> Glb:
        out = tmp_path_factory.mktemp("nested")
        config = load_config(CONFIGS / "nested_blocks.yaml")
        convert(samples_dir / "nested_blocks.dxf", ConvertOptions(config, out))
        return Glb(out / "model.glb")

    def test_repeated_block_is_instanced(self, glb: Glb) -> None:
        assert glb.doc["extensionsUsed"] == ["EXT_mesh_gpu_instancing"]
        node = glb.node("VEGETACION/ARBOL")
        attrs = node["extensions"]["EXT_mesh_gpu_instancing"]["attributes"]
        translations = glb.accessor(attrs["TRANSLATION"])
        cols, rows = s.TREE_GRID
        assert len(translations) == cols * rows  # el reflejado y los anidados se fusionan
        origin = np.array(glb.extras["origin_offset"])
        expected = {(i * 6.0, j * 6.0) for i in range(cols) for j in range(rows)}
        restored = {
            (round(float(t[0] + origin[0]), 3), round(float(-t[2] + origin[1]), 3))
            for t in translations
        }
        assert restored == expected
        assert glb.accessor(attrs["SCALE"]) == pytest.approx(1)

    def test_rejected_copies_are_merged_not_lost(self, glb: Glb) -> None:
        objects = glb.node("VEGETACION")["extras"]["objects"]
        # 1 árbol reflejado + 3 grupos × 3 árboles, 2 objetos (copa + tronco) por árbol
        assert len(objects["height"]) == (1 + s.GROUP_TREES * s.GROUP_INSERTS) * 2

    def test_attribute_dependent_blocks_are_not_instanced(self, glb: Glb) -> None:
        assert "children" not in glb.node("EDIFICIOS")
        assert glb.node("EDIFICIOS")["extras"]["objects"]["height"] == [9.0, 15.0, 21.0]

    def test_lines_layer(self, glb: Glb) -> None:
        prim = glb.primitive("MOBILIARIO", mode=1)
        assert len(glb.accessor(prim["indices"])) == 2 * s.GROUP_INSERTS


def test_instancing_can_be_disabled(samples_dir: Path, tmp_path: Path) -> None:
    raw = (CONFIGS / "nested_blocks.yaml").read_text(encoding="utf-8")
    import yaml

    config = parse_config({**yaml.safe_load(raw), "instancing_min": 0})
    convert(samples_dir / "nested_blocks.dxf", ConvertOptions(config, tmp_path))
    assert "extensionsUsed" not in Glb(tmp_path / "model.glb").doc


def test_hard_edged_meshes_have_flat_normals(samples_dir: Path, tmp_path: Path) -> None:
    run(samples_dir, tmp_path, "faces_3d")
    glb = Glb(tmp_path / "faces_3d" / "model.glb")
    normals = glb.accessor(glb.primitive("CUBO")["attributes"]["NORMAL"])
    axis_aligned = np.isclose(np.abs(normals), 1, atol=1e-6).sum(axis=1)
    assert (axis_aligned == 1).all()  # sin suavizado entre caras del cubo


def test_tiles_share_origin_and_cover_everything(samples_dir: Path, tmp_path: Path) -> None:
    whole = run(samples_dir, tmp_path / "whole", "block_2d")
    tiled = run(samples_dir, tmp_path / "tiled", "block_2d", tile_size=25.0)
    manifest = json.loads((tmp_path / "tiled" / "block_2d" / "manifest.json").read_text())
    assert manifest["schemaVersion"] == 1
    assert len(manifest["tiles"]) == len(tiled.outputs) > 1
    assert sum(t["triangles"] for t in manifest["tiles"]) == whole.outputs[0].stats.triangles
    for tile in manifest["tiles"]:
        glb = Glb(tmp_path / "tiled" / "block_2d" / tile["file"])
        assert glb.extras["origin_offset"] == manifest["origin_offset"]


def test_report_lists_everything(samples_dir: Path, tmp_path: Path) -> None:
    run(samples_dir, tmp_path, "faces_3d")
    report = json.loads((tmp_path / "faces_3d" / "report.json").read_text(encoding="utf-8"))
    assert {r["type"] for r in report["entities"]["unsupported"]} == {"3DSOLID", "BODY"}
    assert report["layers"]["SOLIDOS"]["status"] == "not_convertible"
    assert report["output"]["triangles"] > 0
    assert set(report["timings_s"]) >= {"read", "build", "export"}
    assert any("CONVTOMESH" in w for w in report["warnings"])
    md = (tmp_path / "faces_3d" / "report.md").read_text(encoding="utf-8")
    for section in ("## Avisos", "## Capas", "## No soportadas", "## Tiempos"):
        assert section in md


def test_report_topology_counts(samples_dir: Path, tmp_path: Path) -> None:
    result = run(samples_dir, tmp_path, "dirty")
    topology = result.report["layers"]["EDIFICIOS"]["topology"]
    assert topology["duplicates_removed"] == 1
    assert topology["repaired"] == 1
    assert result.report["layers"]["0"]["status"] == "not_convertible"  # solo un DIMENSION


def test_cli_convert(samples_dir: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    args = ["convert", str(samples_dir / "block_2d.dxf"), "-c", str(CONFIGS / "block_2d.yaml")]
    result = runner.invoke(app, [*args, "-o", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "model.glb").is_file()
    assert "report.md" in result.stdout
    bad = runner.invoke(app, [*args, "-o", str(tmp_path), "--tiles", "0"])
    assert bad.exit_code == USER_ERROR_EXIT


@pytest.mark.parametrize("size", ["nan", "inf", "-inf"])
def test_cli_rejects_nonfinite_tiles_before_reading(size: str, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["convert", "not-read.dxf", "-c", "not-read.yaml", "-o", str(tmp_path), f"--tiles={size}"],
    )
    assert result.exit_code == USER_ERROR_EXIT
    assert "finito" in result.output


def test_glb_magic(utm: tuple[ConvertResult, Glb]) -> None:
    result, _ = utm
    data = result.outputs[0].path.read_bytes()
    assert struct.unpack_from("<4sII", data) == (b"glTF", 2, len(data))


@node_tools
def test_all_samples_pass_gltf_validator(samples_dir: Path, tmp_path: Path) -> None:
    paths = []
    for name in ("block_2d", "nested_blocks", "faces_3d", "dirty"):
        paths.extend(o.path for o in run(samples_dir, tmp_path, name).outputs)
    paths.extend(
        o.path for o in run(samples_dir, tmp_path / "t", "block_2d", tile_size=25.0).outputs
    )
    for report in validate(paths):
        assert report["errors"] == 0, report["messages"]
        assert report["warnings"] == 0, report["messages"]


@node_tools
@pytest.mark.parametrize("method", list(Compression))
def test_compression_keeps_ids_and_validates(
    samples_dir: Path, tmp_path: Path, method: Compression
) -> None:
    raw = run(samples_dir, tmp_path / "raw", "nested_blocks").outputs[0].path
    packed = run(samples_dir, tmp_path / "packed", "nested_blocks", compression=method)
    path = packed.outputs[0].path
    assert path.stat().st_size < raw.stat().st_size
    (report,) = validate([path])
    assert report["errors"] == 0, report["messages"]
    by_name = lambda summary: {p["mesh"]: p["ids"] for p in summary if p["ids"]}  # noqa: E731
    assert by_name(attributes(path)) == by_name(attributes(raw))
    assert packed.report["output"]["compression"] == method.value


def test_large_coordinates_detected_in_read(samples_dir: Path) -> None:
    bbox = read_dxf(samples_dir / "block_2d_utm.dxf").bbox
    assert bbox is not None and bbox.min[0] > 200_000


def test_cli_survives_windows_console_encoding(
    samples_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Consola cp1252 (Windows): el resumen lleva → y no debe tumbar el comando tras convertir."""
    import io
    import sys

    from extrude.cli import _tolerant_console

    for errors in ("strict", "surrogateescape"):
        stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors=errors)
        monkeypatch.setattr(sys, "stdout", stream)
        _tolerant_console()
        print("OK: 1 GLB · 12 triángulos → out", end="")
        stream.flush()
        assert stream.buffer.getvalue() == "OK: 1 GLB · 12 triángulos ? out".encode("cp1252")


@node_tools
@pytest.mark.parametrize("out", [None, "salida relativa ñ"])
@pytest.mark.parametrize("method", list(Compression))
def test_cli_compression_with_relative_output(
    samples_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    out: str | None,
    method: Compression,
) -> None:
    """Node corre con cwd=tools/: las rutas relativas (también la salida por defecto `out`) deben
    resolverse respecto a donde se ejecuta extrude, con espacios y caracteres no ASCII."""
    workdir = tmp_path / "carpeta de trabajo ñ"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    args = [
        "convert",
        str(samples_dir / "nested_blocks.dxf"),
        "-c",
        str(CONFIGS / "nested_blocks.yaml"),
        "--compress",
        method.value,
    ]
    if out is not None:
        args += ["-o", out]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    out_dir = workdir / (out or "out")
    glb = out_dir / "model.glb"
    assert glb.is_file()
    assert not (out_dir / "model.raw.glb").exists()
    extension = {
        Compression.DRACO: "KHR_draco_mesh_compression",
        Compression.MESHOPT: "EXT_meshopt_compression",
    }[method]
    assert extension in Glb(glb).doc.get("extensionsUsed", [])
    (report,) = validate([Path(out or "out") / "model.glb"])  # ruta relativa también aquí
    assert report["errors"] == 0, report["messages"]
