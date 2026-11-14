"""Esquema compacto para Anthropic: la salida estructurada compila el esquema a una gramática con
límites (16 campos con varios tipos, 24 opcionales y un tamaño máximo). El esquema completo los
supera (HTTP 400 real); en el compacto `args` viaja como texto JSON y se valida igual."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from ai_fixtures import write_model
from ai_helpers import FakeProvider, ask, copy_job, ids_of, op, plan, refs, service, targets

from extrude.ai_planner import (
    SYSTEM_PROMPT,
    AiError,
    ProposalRequest,
    compact_raw_schema,
    raw_schema,
)
from extrude.ai_providers import AnthropicProvider

ANTHROPIC_MAX_UNIONS = 16
ANTHROPIC_MAX_OPTIONAL = 24


@dataclass
class CompactFake(FakeProvider):
    compact_schema: bool = True


@pytest.fixture(scope="module")
def model_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_model(tmp_path_factory.mktemp("compact") / "out", kiosks=True)


@pytest.fixture
def job(tmp_path: Path, model_dir: Path) -> Path:
    return copy_job(model_dir, tmp_path / "job")


def _count(schema: Any) -> tuple[int, int]:
    """(campos con varios tipos, campos opcionales), como los cuenta la API de Anthropic."""
    unions = optional = 0
    if isinstance(schema, dict):
        if "anyOf" in schema or isinstance(schema.get("type"), list):
            unions += 1
        if schema.get("type") == "object":
            optional += len(set(schema.get("properties", {})) - set(schema.get("required", [])))
        for value in schema.values():
            u, o = _count(value)
            unions, optional = unions + u, optional + o
    elif isinstance(schema, list):
        for value in schema:
            u, o = _count(value)
            unions, optional = unions + u, optional + o
    return unions, optional


def test_full_schema_exceeds_the_api_limits_and_compact_fits() -> None:
    assert _count(raw_schema())[0] > ANTHROPIC_MAX_UNIONS  # por eso la API respondía 400
    unions, optional = _count(compact_raw_schema())
    assert unions <= ANTHROPIC_MAX_UNIONS and optional <= ANTHROPIC_MAX_OPTIONAL
    operation = compact_raw_schema()["properties"]["operations"]["items"]
    assert operation["properties"]["args"] == {"type": "string"}
    assert "building.set_height" in operation["properties"]["tool"]["enum"]
    assert AnthropicProvider().compact_schema is True


def _height_as_text(ctx: dict[str, Any]) -> str:
    data = json.loads(
        plan(
            op(
                "op1",
                "building.set_height",
                {"mode": "delta_floors", "value": 2, "floorHeightM": None},
                targets(refs(ctx, "E-001")),
            )
        )
    )
    for operation in data["operations"]:
        operation["args"] = json.dumps(operation["args"])  # como lo devuelve el modelo
    return json.dumps(data)


def test_compact_provider_gets_compact_schema_and_args_are_validated_as_usual(job: Path) -> None:
    fake = CompactFake(_height_as_text)
    result = ask(service(fake), job, "Sube E-001 dos plantas")
    [call] = fake.calls
    assert call["schema"] == compact_raw_schema()
    assert call["system"].startswith(SYSTEM_PROMPT) and "building.set_height" in call["system"]
    [item] = result["operations"]
    assert item["objectIds"] == ids_of(job, "E-001") and item["status"] == "ready"
    assert item["impact"]["heights"] == [{"label": "E-001", "from": 12.0, "to": 18.0}]
    # La caché guarda el formato completo: repetir no vuelve a consultar
    assert ask(service(fake), job, "Sube E-001 dos plantas")["cached"] is True


def test_args_that_are_not_json_are_rejected_not_guessed(job: Path) -> None:
    def broken(ctx: dict[str, Any]) -> str:
        data = json.loads(_height_as_text(ctx))
        data["operations"][0]["args"] = "{modo: dos plantas"
        return json.dumps(data)

    with pytest.raises(AiError) as caught:
        ask(service(CompactFake(broken)), job, "Sube E-001 dos plantas")
    assert caught.value.kind == "invalid_response"


def test_consent_preview_shows_the_instructions_actually_sent(job: Path) -> None:
    fake = CompactFake(_height_as_text)
    svc = service(fake)
    request = ProposalRequest.model_validate({"provider": fake.id, "description": "Sube E-001"})
    preview = svc.context_preview("job1", job, request)
    assert preview["system"] != SYSTEM_PROMPT and "building.set_height" in preview["system"]
    plain = FakeProvider(_height_as_text)
    request = ProposalRequest.model_validate({"provider": plain.id, "description": "Sube E-001"})
    assert service(plain).context_preview("job1", job, request)["system"] == SYSTEM_PROMPT
