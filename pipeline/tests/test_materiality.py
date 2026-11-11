"""Esquema y validación de materials.json frente al catálogo y la biblioteca."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_args

import pytest
from typer.testing import CliRunner

from extrude.catalog import CATALOG_NAME
from extrude.cli import app
from extrude.errors import ExtrudeError
from extrude.material_library import DEFAULT_LIBRARY, LIBRARY_VERSION, PRESETS
from extrude.materiality import (
    SurfaceKey,
    effective_assignments,
    load_plan,
    parse_plan,
    validate_plan,
)
from extrude.surfaces import SURFACE_KEYS

REVISION = "sha256:" + "a" * 64
E1 = "obj-0123456789abcdef"
E2 = "obj-fedcba9876543210"
ROAD = "obj-00000000000000aa-2"

CATALOG: dict[str, Any] = {
    "geometryRevision": REVISION,
    "objects": [
        {"id": E1, "label": "E-001", "surfaces": {"roof": {}, "facade_exterior": {}}},
        {"id": E2, "label": "E-002", "surfaces": {"roof": {}, "facade_exterior": {}}},
        {"id": ROAD, "label": "V-001", "surfaces": {"ground": {}}},
    ],
}


def assignment(**extra: Any) -> dict[str, Any]:
    return {
        "objectIds": [E1, E2],
        "surface": "facade_exterior",
        "materialId": "brick_light",
        "materialVersion": "1",
        "parameters": {"tileSizeM": [2.0, 2.0], "rotationDeg": 0, "roughness": 0.85},
        "origin": "ai_reviewed",
        "reason": "Selección norte confirmada por el usuario",
        **extra,
    }


def plan(*assignments: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "projectId": "proyecto-a",
        "geometryRevision": REVISION,
        "materialRevision": "mat-003",
        "catalogVersion": LIBRARY_VERSION,
        "assignments": list(assignments) or [assignment()],
        **extra,
    }


def codes(data: dict[str, Any]) -> list[str]:
    return [i.code for i in validate_plan(parse_plan(json.dumps(data)), CATALOG)]


def test_example_plan_from_requirements_is_valid() -> None:
    data = plan(
        assignment(),
        assignment(objectIds=[E1], surface="roof", materialId="roof_gravel", origin="manual"),
        assignment(objectIds=[ROAD], surface="ground", materialId="stone_grey", parameters={}),
        omissions=[{"text": "Carpinterías negras", "reason": "El DWG no tiene carpinterías"}],
        description="Los edificios del norte tienen fachada de ladrillo claro…",
    )
    parsed = parse_plan(json.dumps(data))
    assert validate_plan(parsed, CATALOG) == []
    table = effective_assignments(parsed)
    assert table[(E2, "facade_exterior")].material_id == "brick_light"
    assert table[(E1, "roof")].origin == "manual"
    assert (E2, "roof") not in table  # sin asignación: conserva el material de la capa


def test_round_trip_and_description_is_optional() -> None:
    parsed = parse_plan(json.dumps(plan(description="texto")))
    again = parse_plan(parsed.to_json())
    assert again == parsed
    # RS-10: borrar la descripción no pierde las asignaciones
    stripped = parse_plan(json.dumps(plan()))
    assert stripped.description is None
    assert stripped.assignments == parsed.assignments


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(extra=1),  # clave desconocida
        lambda p: p.update(schemaVersion=2),
        lambda p: p.update(geometryRevision="md5:abc"),
        lambda p: p.update(materialRevision="3"),
        lambda p: p["assignments"][0].update(objectIds=[]),
        lambda p: p["assignments"][0].update(objectIds=["E-001"]),  # etiqueta, no ID
        lambda p: p["assignments"][0].update(surface="windows"),
        lambda p: p["assignments"][0].update(origin="ai_proposed"),
        lambda p: p["assignments"][0].update(materialId="https://evil.example/brick.png"),
        lambda p: p["assignments"][0].update(materialId="../../etc/passwd"),
        lambda p: p["assignments"][0]["parameters"].update(roughness=1.5),
        lambda p: p["assignments"][0]["parameters"].update(tileSizeM=[0, 2]),
        lambda p: p["assignments"][0]["parameters"].update(tileSizeM=[2]),
        lambda p: p["assignments"][0]["parameters"].update(rotationDeg=1e999),
        lambda p: p["assignments"][0]["parameters"].update(color="red"),
        lambda p: p["assignments"][0]["parameters"].update(textureUrl="http://x/y.png"),
    ],
)
def test_schema_rejects_invalid_values(mutate: Any) -> None:
    data = plan()
    mutate(data)
    with pytest.raises(ExtrudeError):
        parse_plan(json.dumps(data))


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_json_constants_are_rejected(constant: str) -> None:
    text = json.dumps(plan()).replace('"rotationDeg": 0', f'"rotationDeg": {constant}')
    assert constant in text
    with pytest.raises(ExtrudeError, match="no cumple"):
        parse_plan(text)


def test_semantic_validation_against_catalog_and_library() -> None:
    assert codes(plan(geometryRevision="sha256:" + "b" * 64)) == ["revision"]
    assert codes(plan(catalogVersion="library-0")) == ["catalog_version"]
    assert codes(plan(assignment(objectIds=["obj-1111111111111111"]))) == ["unknown_object"]
    assert codes(plan(assignment(objectIds=[ROAD]))) == ["missing_surface"]
    assert codes(plan(assignment(materialId="marble"))) == ["unknown_material"]
    assert codes(plan(assignment(materialVersion="9"))) == ["unknown_material"]
    assert codes(plan(assignment(objectIds=[E1, E1]))) == ["duplicate_object"]


def test_conflicting_assignments_are_not_resolved_by_order() -> None:
    concrete = assignment(objectIds=[E1], materialId="concrete_exposed")
    issues = validate_plan(parse_plan(json.dumps(plan(assignment(), concrete))), CATALOG)
    assert [(i.code, i.assignment) for i in issues] == [("conflict", 1)]
    assert "E-001" in issues[0].message
    with pytest.raises(ValueError, match="Conflicto"):
        effective_assignments(parse_plan(json.dumps(plan(assignment(), concrete))))


def test_surface_enum_matches_geometry_labels() -> None:
    assert set(get_args(SurfaceKey)) == set(SURFACE_KEYS)
    assert {"facade_exterior", "facade_courtyard", "roof", "foundation", "ground", "generic"} <= (
        set(SURFACE_KEYS)
    )


def test_library_has_the_twelve_minimum_presets() -> None:
    assert len(PRESETS) >= 12
    for preset in PRESETS:
        assert DEFAULT_LIBRARY.get(preset.id, preset.version) is preset
        assert all(s > 0 for s in preset.tile_size_m)
        assert 0 <= preset.roughness <= 1 and 0 <= preset.opacity <= 1
        assert preset.color.startswith("#") and len(preset.color) == 7


def test_plan_size_limit(tmp_path: Path) -> None:
    path = tmp_path / "materials.json"
    path.write_text(" " * (5 * 1024 * 1024 + 1), encoding="utf-8")
    with pytest.raises(ExtrudeError, match="máximo"):
        load_plan(path)


def test_cli_validates_plan_against_conversion_catalog(tmp_path: Path) -> None:
    catalog = tmp_path / CATALOG_NAME
    catalog.write_text(json.dumps(CATALOG), encoding="utf-8")
    good = tmp_path / "ok.json"
    good.write_text(json.dumps(plan()), encoding="utf-8")
    result = CliRunner().invoke(app, ["validate-materials", str(good), "--objects", str(catalog)])
    assert result.exit_code == 0, result.output
    assert "Plan válido" in result.output

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(plan(assignment(objectIds=[ROAD]))), encoding="utf-8")
    result = CliRunner().invoke(app, ["validate-materials", str(bad), "--objects", str(catalog)])
    assert result.exit_code == 1
    assert "missing_surface" in result.output

    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    result = CliRunner().invoke(app, ["validate-materials", str(broken), "--objects", str(catalog)])
    assert result.exit_code == 2
