"""Apoyo real sobre pendientes: cubierta horizontal y contacto continuo con el TIN."""

from dataclasses import replace
from pathlib import Path

import ezdxf
import numpy as np
import pytest
import trimesh
from shapely.geometry import Polygon, box
from shapely.geometry.polygon import orient

from extrude.build import _check_ground, build_scene
from extrude.config import parse_config
from extrude.convert import ConvertOptions, convert
from extrude.dxf_reader import read_dxf
from extrude.extrusion import MeshData
from extrude.gltf_export import _object_table
from extrude.surfaces import Surface
from extrude.terrain import TerrainSurface


@pytest.mark.parametrize("patio", [False, True])
@pytest.mark.parametrize("ridge", [False, True])
def test_foundation_is_closed_with_flat_roof_and_terrain_bottom(patio: bool, ridge: bool) -> None:
    points = np.array(
        [[x, y, (20 if x == 5 else 0) if ridge else x * 2] for x in (0, 5, 10) for y in (0, 10)],
        dtype=float,
    )
    terrain = TerrainSurface(points)
    outer = box(1, 1, 9, 9)
    polygon = orient(Polygon(outer.exterior, [box(6, 3, 8, 7).exterior.coords]) if patio else outer)
    _, high = terrain.range_under(polygon)
    mesh = terrain.building(polygon, high, 9, 0, bottom=True)
    merged = trimesh.Trimesh(mesh.vertices, mesh.faces, process=True)
    assert merged.is_watertight
    assert merged.is_winding_consistent
    assert merged.volume > 0
    # Cada punto de la superficie inferior se apoya en el TIN, también en cruces de aristas.
    # Los vértices en la cota de planta son la arista que separa zócalo y fachada.
    z = mesh.vertices[:, 2]
    lower = mesh.vertices[(z < high + 8.99) & ~np.isclose(z, high)]
    assert len(lower)
    assert np.allclose(lower[:, 2], terrain.height(lower[:, :2]), atol=1e-8)
    # Zócalo y fachada se separan en la planta: el zócalo no sube de ella ni la fachada baja.
    surfaces = mesh.face_surfaces
    foundation = mesh.vertices[mesh.faces[surfaces == Surface.FOUNDATION]]
    facade = mesh.vertices[mesh.faces[surfaces == Surface.FACADE_EXTERIOR]]
    assert len(foundation) and len(facade)
    assert foundation[..., 2].max() == pytest.approx(high)
    assert facade[..., 2].min() == pytest.approx(high)
    assert (surfaces == Surface.FACADE_COURTYARD).any() == patio
    assert (surfaces == Surface.UNDERSIDE).any()
    assert set(np.unique(surfaces)) <= {
        Surface.ROOF,
        Surface.FOUNDATION,
        Surface.FACADE_EXTERIOR,
        Surface.FACADE_COURTYARD,
        Surface.UNDERSIDE,
    }
    assert mesh.vertices[:, 2].max() == pytest.approx(high + 9)
    # Centros de caras inferiores: también siguen las crestas interiores, no solo las esquinas.
    triangles = mesh.vertices[mesh.faces]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    centers = triangles[normals[:, 2] < -1e-8].mean(axis=1)
    assert len(centers)
    assert np.allclose(centers[:, 2], terrain.height(centers[:, :2]), atol=1e-8)


def site(tmp_path: Path, **extra: object):
    doc = ezdxf.new()
    doc.units = 6
    msp = doc.modelspace()
    for x, y in ((0, 0), (10, 0), (0, 10), (10, 10)):
        msp.add_point((x, y, x * 2), dxfattribs={"layer": "T"})
    block = doc.blocks.new("B")
    block.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True)
    msp.add_blockref("B", (0, 0, 5), dxfattribs={"layer": "E"})
    path = tmp_path / "slope.dxf"
    doc.saveas(path)
    config = parse_config(
        {
            "layers": [
                {"match": "T", "kind": "terrain"},
                {"match": "E", "kind": "building", "height": 9, "base_from_terrain": True, **extra},
            ]
        }
    )
    return build_scene(read_dxf(path), config)


def test_default_building_keeps_height_and_adds_support(tmp_path: Path) -> None:
    result = site(tmp_path)
    layer = result.layers["E"]
    (obj,) = layer.objects
    assert obj.height == 9
    assert obj.floor_elevation == 20
    assert obj.foundation_height == 20
    assert obj.mesh.vertices[:, 2].min() == 0
    assert obj.mesh.vertices[:, 2].max() == 29
    assert obj.instance is None  # zócalos distintos no deben compartir geometría
    assert obj.source_insert is not None
    exported = _object_table([obj], layer.rule)
    assert exported["height"] == [9]
    assert exported["floors"] == [3]
    assert exported["base"] == [20]
    assert exported["foundation_height"] == [20]
    assert exported["handle"] == [obj.source_insert.handle.partition("#")[0]]
    assert layer.stats.ground.as_dict() == {
        "checked": 1,
        "floating": 0,
        "buried": 0,
        "intersecting": 0,
        "partially_buried": 0,
    }
    (detail,) = layer.stats.ground_details
    assert detail["category"] == "foundation"
    assert detail["base_mode"] == "terrain_foundation"
    assert detail["contact_clearance_m"] == pytest.approx([0, 0])


@pytest.mark.parametrize(
    "shift, category", [(3, "floating"), (-3, "intersecting"), (-15, "partially_buried")]
)
def test_ground_check_detects_broken_support_in_actual_mesh(
    tmp_path: Path,
    shift: float,
    category: str,
) -> None:
    result = site(tmp_path)
    (obj,) = result.layers["E"].objects
    vertices = obj.mesh.vertices.copy()
    vertices[:, 2] += shift
    result.layers["E"].objects[:] = [replace(obj, mesh=MeshData(vertices, obj.mesh.faces))]
    terrain = TerrainSurface(
        np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 20.0], [0.0, 10.0, 0.0], [10.0, 10.0, 20.0]])
    )
    _check_ground(result, terrain)
    assert result.layers["E"].stats.ground.as_dict()[category] == 1


def test_verified_source_base_is_preserved(tmp_path: Path) -> None:
    result = site(tmp_path, base_from_terrain=False, base_from_elevation=True)
    (obj,) = result.layers["E"].objects
    assert obj.floor_elevation is None
    assert obj.foundation_height == 0
    assert obj.mesh.vertices[:, 2].min() == 5
    assert obj.mesh.vertices[:, 2].max() == 14


@pytest.mark.parametrize("bottom", [False, True])
def test_zero_height_building_over_interior_valley_is_a_terrain_surface(
    tmp_path: Path,
    bottom: bool,
) -> None:
    doc = ezdxf.new()
    doc.units = 6
    msp = doc.modelspace()
    points = [(0, 0, 10), (10, 0, 10), (10, 10, 10), (0, 10, 10), (5, 5, 0)]
    for point in points:
        msp.add_point(point, dxfattribs={"layer": "T"})
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "E"})
    source = tmp_path / "zero.dxf"
    doc.saveas(source)
    config = parse_config(
        {
            "layers": [
                {"match": "T", "kind": "terrain"},
                {
                    "match": "E",
                    "kind": "building",
                    "height": 0,
                    "base_from_terrain": True,
                    "bottom_cap": bottom,
                },
            ]
        }
    )
    result = convert(source, ConvertOptions(config, tmp_path / "out"))
    (obj,) = result.build.layers["E"].objects
    assert obj.height == 0
    assert obj.draped
    assert obj.floor_elevation is None
    assert obj.foundation_height == 0
    terrain = TerrainSurface(np.array(points, dtype=float))
    assert obj.mesh.vertices[:, 2].min() == 0
    assert obj.mesh.vertices[:, 2].max() == 10
    centers = obj.mesh.vertices[obj.mesh.faces].mean(axis=1)
    assert np.allclose(centers[:, 2], terrain.height(centers[:, :2]))
    layer = result.report["layers"]["E"]
    assert layer["terrain_foundations"] == 0
    assert layer["ground_check"] == {
        "checked": 1,
        "floating": 0,
        "buried": 0,
        "intersecting": 0,
        "partially_buried": 0,
    }
    assert (tmp_path / "out" / "model.glb").is_file()


def test_foundation_without_lower_points_is_reported_without_empty_reduction(
    tmp_path: Path,
) -> None:
    result = site(tmp_path)
    (obj,) = result.layers["E"].objects
    vertices = obj.mesh.vertices.copy()
    vertices[:, 2] = vertices[:, 2].max()
    result.layers["E"].objects[:] = [replace(obj, mesh=MeshData(vertices, obj.mesh.faces))]
    terrain = TerrainSurface(
        np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 20.0], [0.0, 10.0, 0.0], [10.0, 10.0, 20.0]])
    )
    _check_ground(result, terrain)
    assert any("no se pudo comprobar el contacto" in warning for warning in result.warnings)
    assert result.layers["E"].stats.ground.floating == 1
    (detail,) = result.layers["E"].stats.ground_details
    assert detail["category"] == "floating"
    assert detail["contact_clearance_m"] is None
