"""Política general de cotas, alturas y apoyo (edificios flotantes).

Fixtures sintéticas con estructuras distintas; ninguna usa nombres, coordenadas ni cotas de un
plano real. Cubre: terreno con cotas positivas y negativas; huellas con Z cero, positiva,
negativa y mixta; alturas fiables y provisionales; ausencia de terreno y terreno inválido;
bases y cubiertas explícitas; unidades; bloques; patios; varias capas y formatos de terreno;
huellas fuera del TIN; clasificación incierta; y diagnóstico de contacto.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any

import ezdxf
import numpy as np
import pytest
import yaml
from ezdxf.layouts import Modelspace

from extrude.build import HIGH_FOUNDATION_M, BuildResult, build_scene
from extrude.classification import guess_kind, layer_evidence
from extrude.config import Config, ConfigError, Kind, parse_config
from extrude.convert import ConvertOptions, convert
from extrude.dxf_reader import read_dxf
from extrude.heights import HeightOrigin, confidence
from extrude.init_config import draft_config
from extrude.terrain import GROUND_TOLERANCE_M

Draw = Callable[[Modelspace], None]


def drawing(tmp_path: Path, draw: Draw, units: int = 6, name: str = "plano.dxf") -> Path:
    doc = ezdxf.new("R2018")
    doc.units = units
    draw(doc.modelspace())
    path = tmp_path / name
    doc.saveas(path)
    return path


def slope(msp: Modelspace, z0: float, layer: str = "TOPO", scale: float = 1.0) -> None:
    """Ladera de 100 × 100 m que sube 20 m de oeste a este desde la cota z0."""
    for x in range(0, 101, 10):
        msp.add_lwpolyline(
            [(x * scale, 0), (x * scale, 100 * scale)],
            dxfattribs={"layer": layer, "elevation": (z0 + x / 5) * scale},
        )


def square(
    msp: Modelspace, x: float, y: float, layer: str, z: float = 0.0, size: float = 10.0
) -> None:
    msp.add_lwpolyline(
        [(x, y), (x + size, y), (x + size, y + size), (x, y + size)],
        close=True,
        dxfattribs={"layer": layer, "elevation": z},
    )


def rules(*layers: dict[str, Any]) -> Config:
    return parse_config({"layers": list(layers)})


def auto_building(**extra: Any) -> dict[str, Any]:
    return {"match": "EDIF", "kind": "building", "base_auto": True, **extra}


TERRAIN = {"match": "TOPO", "kind": "terrain"}


def build(path: Path, config: Config) -> BuildResult:
    return build_scene(read_dxf(path), config)


def z_range(obj: Any) -> tuple[float, float]:
    z = obj.mesh.vertices[:, 2]
    return float(z.min()), float(z.max())


# --- Apoyo automático por objeto ---------------------------------------------------------------


@pytest.mark.parametrize("terrain_z0", [-80.0, 0.0, 250.0])
@pytest.mark.parametrize("footprint_z", [0.0, 35.0, -45.0])
def test_auto_support_seats_every_building_on_its_own_terrain(
    tmp_path: Path, terrain_z0: float, footprint_z: float
) -> None:
    def draw(msp: Modelspace) -> None:
        slope(msp, terrain_z0)
        square(msp, 10, 10, "EDIF", footprint_z)  # terreno z0+2 … z0+4
        square(msp, 70, 60, "EDIF", footprint_z)  # terreno z0+14 … z0+16

    result = build(drawing(tmp_path, draw), rules(TERRAIN, auto_building(height=12)))
    layer = result.layers["EDIF"]
    low, high = sorted(layer.objects, key=lambda o: o.floor_elevation or 0)
    # Cada edificio en su cota: no hay un desplazamiento común para toda la capa
    assert low.floor_elevation == pytest.approx(terrain_z0 + 4)
    assert high.floor_elevation == pytest.approx(terrain_z0 + 16)
    for obj in (low, high):
        base, top = z_range(obj)
        assert top == pytest.approx((obj.floor_elevation or 0) + 12)  # altura desde la planta
        assert base == pytest.approx((obj.floor_elevation or 0) - 2)  # zócalo hasta el terreno
        assert obj.base_origin == "terrain"
        assert obj.height_origin is HeightOrigin.FIXED
    check = layer.stats.ground
    assert check is not None and check.checked == 2
    assert (check.floating, check.buried, check.partial_gap, check.unverified) == (0, 0, 0, 0)


def test_mixed_footprint_elevations_are_reported_not_trusted(tmp_path: Path) -> None:
    def draw(msp: Modelspace) -> None:
        slope(msp, -60.0)
        square(msp, 10, 10, "EDIF", 0.0)  # sin cota
        square(msp, 30, 10, "EDIF", -53.0)  # coincide con el terreno (-54 … -52)
        square(msp, 50, 10, "EDIF", 40.0)  # muy por encima
        square(msp, 70, 10, "EDIF", -90.0)  # por debajo

    result = build(drawing(tmp_path, draw), rules(TERRAIN, auto_building(default_height=9)))
    layer = result.layers["EDIF"]
    assert sorted(o.source_z_role or "" for o in layer.objects) == [
        "above_terrain",
        "below_terrain",
        "matches_terrain",
        "none",
    ]
    assert layer.stats.ground is not None and layer.stats.ground.floating == 0
    assert layer.stats.z_unexplained == 2
    (warning,) = [w for w in result.warnings if "cota Z propia" in w]
    assert "2 huellas" in warning and "se apoyan en el terreno" in warning
    # La altura sigue siendo provisional: apoyar no la convierte en un dato
    assert {confidence(o.height_origin) for o in layer.objects} == {"provisional"}


@pytest.mark.parametrize(
    ("footprint_z", "base", "origin"),
    [
        (0.0, 0.0, "fixed"),
        (35.0, 35.0, "source_elevation_inferred"),
        (-12.0, -12.0, "source_elevation_inferred"),
    ],
)
def test_auto_support_without_terrain_uses_the_footprint_elevation(
    tmp_path: Path, footprint_z: float, base: float, origin: str
) -> None:
    path = drawing(tmp_path, lambda msp: square(msp, 0, 0, "EDIF", footprint_z))
    result = build(path, rules(auto_building(height=6)))
    (obj,) = result.layers["EDIF"].objects
    assert z_range(obj) == pytest.approx((base, base + 6))
    assert obj.base_origin == origin
    assert result.terrain["status"] == "absent"
    assert result.layers["EDIF"].stats.ground is None  # sin terreno no se acredita contacto


def test_explicit_base_is_respected_and_its_conflict_reported(tmp_path: Path) -> None:
    def draw(msp: Modelspace) -> None:
        slope(msp, 100.0)
        square(msp, 10, 10, "EDIF", 0.0)  # planta sin cota en un terreno a +100

    config = rules(
        TERRAIN, {"match": "EDIF", "kind": "building", "base_from_elevation": True, "height": 9}
    )
    result = build(drawing(tmp_path, draw), config)
    (obj,) = result.layers["EDIF"].objects
    assert z_range(obj) == pytest.approx((0.0, 9.0))  # no se mueve en silencio
    assert obj.base_origin == "source_elevation"
    check = result.layers["EDIF"].stats.ground
    assert check is not None and check.buried == 1
    assert any("usa base_from_elevation pero" in w for w in result.warnings)


def test_base_flags_are_exclusive() -> None:
    for pair in (("base_auto", "base_from_terrain"), ("base_auto", "base_from_elevation")):
        with pytest.raises(ConfigError):
            rules({"match": "E", "kind": "building", "height": 3, pair[0]: True, pair[1]: True})


# --- Cubierta absoluta frente a altura relativa ------------------------------------------------


def test_absolute_roof_over_terrain_is_not_added_to_the_base(tmp_path: Path) -> None:
    """Terreno en -52/-54 y cubierta en la cota absoluta -30: la altura es la diferencia con la
    planta (22 m), no la cota tomada como altura relativa."""

    def draw(msp: Modelspace) -> None:
        slope(msp, -60.0)
        square(msp, 30, 10, "EDIF", -30.0)

    config = rules(TERRAIN, auto_building(height_from="elevation", default_height=9))
    result = build(drawing(tmp_path, draw), config)
    (obj,) = result.layers["EDIF"].objects
    assert obj.floor_elevation == pytest.approx(-52.0)
    assert obj.height == pytest.approx(22.0)
    assert obj.height_origin is HeightOrigin.ELEVATION
    assert z_range(obj)[1] == pytest.approx(-30.0)  # la cubierta queda en su cota absoluta
    assert confidence(obj.height_origin) == "inferred"


@pytest.mark.parametrize("roof_z", [-52.0, -70.0, 0.0])
def test_roof_at_or_below_the_base_gives_no_height(tmp_path: Path, roof_z: float) -> None:
    def draw(msp: Modelspace) -> None:
        slope(msp, -60.0)
        square(msp, 30, 10, "EDIF", roof_z)

    config = rules(TERRAIN, auto_building(height_from="elevation", default_height=9))
    result = build(drawing(tmp_path, draw), config)
    (obj,) = result.layers["EDIF"].objects
    # También Z = 0: es la ausencia de cota, no una cubierta 52 m por encima del terreno
    assert obj.height == 9.0 and obj.height_origin is HeightOrigin.DEFAULT
    assert result.layers["EDIF"].stats.elevation_rejected == 1
    assert any("cota de cubierta en la base o por debajo" in w for w in result.warnings)
    assert z_range(obj)[1] == pytest.approx(-52.0 + 9.0)


def test_elevation_height_without_terrain_keeps_its_meaning(tmp_path: Path) -> None:
    path = drawing(tmp_path, lambda msp: square(msp, 0, 0, "EDIF", 14.0))
    config = rules({"match": "EDIF", "kind": "building", "height_from": "elevation"})
    (obj,) = build(path, config).layers["EDIF"].objects
    assert z_range(obj) == pytest.approx((0.0, 14.0))


def test_reliable_heights_survive_the_support_policy(tmp_path: Path) -> None:
    def draw(msp: Modelspace) -> None:
        slope(msp, 30.0)
        for x, label in ((10, "4P"), (40, "7P")):
            square(msp, x, 10, "EDIF", -5.0)
            msp.add_text(label, dxfattribs={"layer": "ROTULOS", "insert": (x + 4, 15)})
        square(msp, 70, 10, "EDIF", -5.0)  # sin rótulo

    config = rules(TERRAIN, auto_building(height_from="text", floor_height=3.0, default_height=9))
    layer = build(drawing(tmp_path, draw), config).layers["EDIF"]
    by_height = sorted((o.height, confidence(o.height_origin)) for o in layer.objects)
    assert by_height == [(9.0, "provisional"), (12.0, "verified"), (21.0, "verified")]
    assert layer.stats.ground is not None and layer.stats.ground.floating == 0


# --- Unidades, bloques, patios ------------------------------------------------------------------


def test_millimetre_drawings_get_the_same_result_in_metres(tmp_path: Path) -> None:
    def draw(msp: Modelspace) -> None:
        slope(msp, -60.0, scale=1000.0)
        square(msp, 10_000, 10_000, "EDIF", 0.0, size=10_000)

    result = build(drawing(tmp_path, draw, units=4), rules(TERRAIN, auto_building(height=12)))
    (obj,) = result.layers["EDIF"].objects
    assert obj.floor_elevation == pytest.approx(-56.0)
    assert z_range(obj) == pytest.approx((-58.0, -44.0))
    assert result.terrain["z_min_m"] == pytest.approx(-60.0)


def test_courtyards_get_foundations_on_both_rings(tmp_path: Path) -> None:
    def draw(msp: Modelspace) -> None:
        slope(msp, 10.0)
        square(msp, 10, 10, "EDIF", 0.0, size=40)
        square(msp, 25, 25, "EDIF", 0.0, size=10)  # patio

    layer = build(drawing(tmp_path, draw), rules(TERRAIN, auto_building(height=9))).layers["EDIF"]
    (obj,) = layer.objects
    assert obj.footprint is not None and len(obj.footprint.interiors) == 1
    assert obj.foundation_height == pytest.approx(8.0)  # 40 m de huella en una ladera del 20 %
    check = layer.stats.ground
    assert check is not None and (check.floating, check.partial_gap, check.unverified) == (0, 0, 0)
    detail = layer.stats.ground_details[0]
    low, high = detail["contact_clearance_m"]  # type: ignore[misc]
    assert abs(low) < 1e-6 and abs(high) < 1e-6  # el zócalo toca el terreno en todo el contorno


def test_block_instances_are_seated_one_by_one(tmp_path: Path) -> None:
    def draw(msp: Modelspace) -> None:
        slope(msp, -40.0)
        block = msp.doc.blocks.new("CASA")
        block.add_lwpolyline(
            [(0, 0), (8, 0), (8, 8), (0, 8)], close=True, dxfattribs={"layer": "EDIF"}
        )
        for x in (5, 45, 85):
            msp.add_blockref("CASA", (x, 20, 0), dxfattribs={"layer": "EDIF"})

    layer = build(drawing(tmp_path, draw), rules(TERRAIN, auto_building(height=6))).layers["EDIF"]
    floors = sorted(o.floor_elevation or 0 for o in layer.objects)
    assert floors == pytest.approx([-40 + 13 / 5, -40 + 53 / 5, -40 + 93 / 5])
    assert layer.stats.ground is not None and layer.stats.ground.floating == 0


# --- Terreno realmente utilizado -----------------------------------------------------------------


def test_terrain_named_layer_without_usable_surface_is_not_a_terrain(tmp_path: Path) -> None:
    def flat(msp: Modelspace) -> None:  # curvas sin cota: todo a Z = 0
        for x in (0, 50, 100):
            msp.add_lwpolyline([(x, 0), (x, 100)], dxfattribs={"layer": "TOPO"})
        square(msp, 10, 10, "EDIF", 25.0)

    def collinear(msp: Modelspace) -> None:
        msp.add_lwpolyline([(0, 0), (100, 0)], dxfattribs={"layer": "TOPO", "elevation": 12})
        square(msp, 10, 10, "EDIF", 25.0)

    for draw, reason in ((flat, "todas sus cotas son 0"), (collinear, "alineados")):
        result = build(drawing(tmp_path, draw), rules(TERRAIN, auto_building(height=6)))
        assert result.terrain["status"] == "invalid" and reason in result.terrain["reason"]
        (obj,) = result.layers["EDIF"].objects
        assert obj.base_origin == "source_elevation_inferred"  # no se apoya en un terreno falso
        assert z_range(obj)[0] == pytest.approx(25.0)
        assert any("no sirve para apoyar" in w for w in result.warnings)


def test_explicit_terrain_support_without_surface_says_why(tmp_path: Path) -> None:
    def draw(msp: Modelspace) -> None:
        msp.add_lwpolyline([(0, 0), (100, 0)], dxfattribs={"layer": "TOPO", "elevation": 12})
        square(msp, 10, 10, "EDIF")

    config = rules(
        TERRAIN, {"match": "EDIF", "kind": "building", "base_from_terrain": True, "height": 6}
    )
    result = build(drawing(tmp_path, draw), config)
    (warning,) = [w for w in result.warnings if w.startswith("base_from_terrain sin terreno")]
    assert "alineados" in warning


def test_points_meshes_and_several_layers_feed_the_same_support_surface(tmp_path: Path) -> None:
    def draw(msp: Modelspace) -> None:
        for x, y in ((0, 0), (50, 0), (0, 50), (50, 50)):  # puntos acotados
            msp.add_point((x, y, 20.0), dxfattribs={"layer": "TOPO"})
        # Malla 3D en otra capa de terreno: sigue subiendo hasta +40
        msp.add_3dface(
            [(50, 0, 20), (100, 0, 40), (100, 50, 40), (50, 50, 20)], dxfattribs={"layer": "MDT"}
        )
        square(msp, 80, 20, "EDIF")  # solo sobre la malla

    config = rules(TERRAIN, {"match": "MDT", "kind": "terrain"}, auto_building(height=6))
    result = build(drawing(tmp_path, draw), config)
    assert result.terrain["status"] == "valid" and result.terrain["layers"] == ["MDT", "TOPO"]
    (obj,) = result.layers["EDIF"].objects
    # Apoyado sobre la malla (cotas 32 … 36), no sobre una extrapolación de los puntos a 20
    assert obj.floor_elevation == pytest.approx(36.0)
    assert obj.base_origin == "terrain"
    assert any("2 capas de terreno" in w for w in result.warnings)
    # La capa de malla no recibe además un plano base que taparía la topografía dibujada
    assert [o.source_type for o in result.layers["MDT"].objects] == ["3DFACE"]


def test_conflicting_duplicate_samples_are_reported(tmp_path: Path) -> None:
    def draw(msp: Modelspace) -> None:
        slope(msp, 0.0)
        msp.add_point((50, 50, 99.0), dxfattribs={"layer": "TOPO"})  # sobre una curva a +10
        square(msp, 10, 10, "EDIF")

    result = build(drawing(tmp_path, draw), rules(TERRAIN, auto_building(height=6)))
    assert result.terrain["conflicting_duplicates"] == 0  # (50, 50) no es vértice de la curva
    doubled = drawing(
        tmp_path,
        lambda msp: (slope(msp, 0.0), msp.add_point((50, 0, 99.0), dxfattribs={"layer": "TOPO"})),
        name="dup.dxf",
    )
    info = build(doubled, rules(TERRAIN)).terrain
    assert info["conflicting_duplicates"] == 1
    assert any("varias cotas distintas" in w for w in build(doubled, rules(TERRAIN)).warnings)


def test_footprints_outside_the_terrain_are_flagged_as_extrapolated(tmp_path: Path) -> None:
    def draw(msp: Modelspace) -> None:
        slope(msp, 10.0)
        square(msp, 10, 10, "EDIF")  # dentro
        square(msp, 95, 10, "EDIF")  # a caballo del borde
        square(msp, 200, 10, "EDIF")  # fuera por completo

    layer = build(drawing(tmp_path, draw), rules(TERRAIN, auto_building(height=6))).layers["EDIF"]
    assert sorted(o.base_origin or "" for o in layer.objects) == [
        "terrain",
        "terrain_extrapolated",
        "terrain_extrapolated",
    ]
    assert layer.stats.terrain_coverage == {
        "checked": 3,
        "covered": 1,
        "extrapolated": 2,
        "unknown": 0,
    }


# --- Diagnóstico de contacto ----------------------------------------------------------------------


def _fixed_base(tmp_path: Path, base: float, height: float = 6.0) -> Any:
    def draw(msp: Modelspace) -> None:
        slope(msp, 0.0)
        square(msp, 40, 40, "EDIF", size=20)  # terreno 8 … 12 bajo la huella

    config = rules(
        TERRAIN, {"match": "EDIF", "kind": "building", "base_elevation": base, "height": height}
    )
    result = build(drawing(tmp_path, draw), config)
    return result.layers["EDIF"].stats.ground, result


@pytest.mark.parametrize(
    ("base", "height", "category"),
    [
        (30.0, 6.0, "floating"),  # toda la huella en el aire
        (11.5, 6.0, "partial_gap"),  # toca arriba, 3,5 m en el aire abajo
        (4.0, 20.0, "intersecting"),  # la ladera corta la fachada
        (0.0, 9.5, "partially_buried"),  # el terreno supera la cubierta en parte
        (-20.0, 6.0, "buried"),  # cubierta bajo todo el terreno
    ],
)
def test_contact_categories_are_distinct(
    tmp_path: Path, base: float, height: float, category: str
) -> None:
    check, result = _fixed_base(tmp_path, base, height)
    counts = {k: v for k, v in check.as_dict().items() if k != "checked" and v}
    assert counts == {category: 1}
    details = result.layers["EDIF"].stats.ground_details
    assert [d["category"] for d in details] == [category]


def test_a_flat_base_within_tolerance_everywhere_is_supported(tmp_path: Path) -> None:
    def draw(msp: Modelspace) -> None:
        slope(msp, 0.0)
        square(msp, 40, 40, "EDIF", size=4)  # terreno 8 … 8,8 bajo la huella

    config = rules(
        TERRAIN, {"match": "EDIF", "kind": "building", "base_elevation": 8.5, "height": 6}
    )
    layer = build(drawing(tmp_path, draw), config).layers["EDIF"]
    assert layer.stats.ground is not None
    assert {k: v for k, v in layer.stats.ground.as_dict().items() if v} == {"checked": 1}
    assert [d["category"] for d in layer.stats.ground_details] == ["supported"]


def test_contact_tolerance_is_one_metre_and_is_not_widened() -> None:
    assert GROUND_TOLERANCE_M == 1.0
    assert HIGH_FOUNDATION_M == 3.0


# --- Clasificación y capas pendientes -----------------------------------------------------------


@pytest.mark.parametrize(
    ("layer", "kind"),
    [
        ("A-TORRES", Kind.BUILDING),
        ("VIVIENDAS_UNIFAMILIARES", Kind.BUILDING),
        ("NAVES INDUSTRIALES", Kind.BUILDING),
        ("LAGUNA", Kind.WATER),
        ("MOBILIARIO", Kind.IGNORE),
        ("EQ_SANITARIO", Kind.IGNORE),
        ("0", Kind.IGNORE),
    ],
)
def test_names_are_only_one_piece_of_evidence(layer: str, kind: Kind) -> None:
    assert guess_kind(layer) is kind


def _mixed_site(msp: Modelspace) -> None:
    slope(msp, -20.0)
    square(msp, 10, 10, "EDIF")
    for x in (40, 60):
        square(msp, x, 10, "ZONA-7")  # nombre desconocido con contornos cerrados
    msp.add_text("Leyenda", dxfattribs={"layer": "NOTAS", "insert": (0, 0)})
    msp.add_line((0, 0), (5, 5), dxfattribs={"layer": "EJES"})


def test_unknown_layers_with_outlines_are_pending_not_converted_nor_hidden(tmp_path: Path) -> None:
    path = drawing(tmp_path, _mixed_site)
    read = read_dxf(path)
    text = draft_config(read)
    config = parse_config(yaml.safe_load(text))
    assert config.rule_for("ZONA-7").kind is Kind.IGNORE  # type: ignore[union-attr]
    assert "PENDIENTE DE REVISIÓN" in text
    evidence = layer_evidence(read)
    assert evidence["ZONA-7"].closed_outlines == 2 and evidence["EJES"].convertible == 0

    (tmp_path / "c.yaml").write_text(text, encoding="utf-8")
    report = convert(path, ConvertOptions(config, tmp_path / "out")).report
    summary = report["summary"]
    assert summary["converted_objects"] == 2  # edificio + terreno
    assert summary["pending_layers"] == 1 and summary["pending_elements"] == 2
    assert summary["ignored_layers"] >= 2
    assert [p["name"] for p in report["pending_layers"]] == ["ZONA-7"]
    assert report["layers"]["ZONA-7"]["pending_review"] is True
    assert report["layers"]["EJES"]["pending_review"] is False  # nada convertible
    assert summary["ground"]["floating"] == 0  # …de lo convertido: lo pendiente va aparte
    assert any("pendientes de revisión" in w and "ZONA-7" in w for w in report["warnings"])
    markdown = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "pendientes de revisión" in markdown and "ZONA-7: 2 contornos cerrados" in markdown


def test_a_reviewed_layer_stops_being_pending(tmp_path: Path) -> None:
    path = drawing(tmp_path, _mixed_site)
    config = rules(
        TERRAIN,
        auto_building(height=6),
        {"match": "ZONA-7", "kind": "ignore", "reviewed": True},
        {"match": "*", "kind": "ignore"},
    )
    report = convert(path, ConvertOptions(config, tmp_path / "out")).report
    assert report["pending_layers"] == [] and report["summary"]["pending_layers"] == 0


# --- Borrador automático: importaciones nuevas sin tocar YAML ------------------------------------


@pytest.mark.parametrize("terrain_z0", [-70.0, 5.0, 400.0])
def test_new_imports_are_supported_without_editing_anything(
    tmp_path: Path, terrain_z0: float
) -> None:
    def draw(msp: Modelspace) -> None:
        slope(msp, terrain_z0, layer="CURVAS DE NIVEL")
        for x, z in ((10, 0.0), (40, terrain_z0), (70, -999.0)):
            square(msp, x, 30, "EDIFICACION", z)

    path = drawing(tmp_path, draw)
    read = read_dxf(path)
    config = parse_config(yaml.safe_load(draft_config(read)))
    report = convert(path, ConvertOptions(config, tmp_path / "out")).report
    info = report["layers"]["EDIFICACION"]
    assert info["base_mode"] == "auto" and info["base_origins"] == {"terrain": 3}
    assert info["ground_check"]["floating"] == 0 and info["ground_check"]["buried"] == 0
    assert info["heights"] == {"default_height": 3}
    assert report["summary"]["provisional_heights"] == 3
    assert report["summary"]["verified_heights"] == 0
    assert report["terrain"]["status"] == "valid"
    assert any("PROVISIONAL" in w for w in report["warnings"])

    # La geometría exportada, no solo la declarada: cada edificio del GLB toca su terreno
    glb = (tmp_path / "out" / "model.glb").read_bytes()
    length = struct.unpack("<I", glb[12:16])[0]
    gltf = json.loads(glb[20 : 20 + length])
    node = next(n for n in gltf["nodes"] if (n.get("extras") or {}).get("layer") == "EDIFICACION")
    table = node["extras"]["objects"]
    assert table["base_origin"] == ["terrain"] * 3
    assert table["height_confidence"] == ["provisional"] * 3
    assert sorted(table["base"]) == pytest.approx(
        [terrain_z0 + 4, terrain_z0 + 10, terrain_z0 + 16]
    )
    assert node["extras"]["ground_check"]["floating"] == 0
    assert gltf["scenes"][0]["extras"]["geometry_revision"] == report["geometry_revision"]


def test_high_foundations_are_flagged_for_review(tmp_path: Path) -> None:
    def draw(msp: Modelspace) -> None:
        slope(msp, 0.0)
        square(msp, 10, 10, "EDIF", size=10)  # zócalo de 2 m
        square(msp, 40, 10, "EDIF", size=40)  # zócalo de 8 m

    report = convert(
        drawing(tmp_path, draw),
        ConvertOptions(rules(TERRAIN, auto_building(height=9)), tmp_path / "o"),
    ).report
    info = report["layers"]["EDIF"]
    assert info["terrain_foundations"] == 2 and info["high_foundations"] == 1
    assert report["summary"]["high_foundations"] == 1
    assert report["summary"]["max_foundation_height_m"] == pytest.approx(8.0)
    assert any("zócalo de más de 3 m" in w and "implantación" in w for w in report["warnings"])
    assert np.isclose(info["max_foundation_height_m"], 8.0)
