"""Contrato común de los adaptadores reales (SDK oficiales de Anthropic y OpenAI) contra un
servidor HTTP local que imita cada API: sin red ni llamadas facturables.

Comprueba que ambos producen la misma propuesta validada, que la petición usa la salida
estructurada de cada proveedor, que la clave solo viaja en la cabecera y que un error o una
respuesta truncada no provoca reintentos (los SDK reintentan por defecto: aquí `max_retries=0`).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from ai_fixtures import write_model
from ai_helpers import ask, brick, ids_of

from extrude.ai_planner import AiError, AiLimits, AiService
from extrude.ai_providers import AnthropicProvider, OpenAIProvider

pytest.importorskip("anthropic")
pytest.importorskip("openai")

KEY = "sk-test-SECRET-KEY"


@dataclass
class Stub:
    mode: str = "ok"  # ok | truncated | 500 | 401
    requests: list[dict[str, Any]] = field(default_factory=list)


def _anthropic(stub: Stub, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    text = brick("E-001")(json.loads(body["messages"][0]["content"]))
    return 200, {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": body["model"],
        "content": [{"type": "text", "text": text}],
        "stop_reason": "max_tokens" if stub.mode == "truncated" else "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 1000, "output_tokens": 200},
    }


def _openai(stub: Stub, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    text = brick("E-001")(json.loads(body["input"]))
    truncated = stub.mode == "truncated"
    return 200, {
        "id": "resp_1",
        "object": "response",
        "created_at": 1_700_000_000,
        "status": "incomplete" if truncated else "completed",
        "model": body["model"],
        "incomplete_details": {"reason": "max_output_tokens"} if truncated else None,
        "output": []
        if truncated
        else [
            {
                "type": "message",
                "id": "msg_1",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 200,
            "total_tokens": 1200,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
        "error": None,
        "instructions": None,
        "metadata": {},
        "parallel_tool_calls": True,
        "temperature": None,
        "tool_choice": "auto",
        "tools": [],
        "top_p": None,
    }


@pytest.fixture
def stub() -> Iterator[tuple[str, Stub]]:
    state = Stub()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.requests.append(
                {
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body,
                }
            )
            if state.mode in ("500", "401"):
                status, payload = int(state.mode), {"error": {"type": "x", "message": "no"}}
            elif self.path.endswith("/messages"):
                status, payload = _anthropic(state, body)
            else:
                status, payload = _openai(state, body)
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", state
    server.shutdown()
    server.server_close()


@pytest.fixture
def job(tmp_path_factory: pytest.TempPathFactory, tmp_path: Path) -> Path:
    model = tmp_path_factory.getbasetemp() / "providers-model"
    if not (model / "out" / "model.glb").is_file():
        write_model(model / "out")
    job_dir = tmp_path / "job"
    (job_dir / "out").mkdir(parents=True)
    for name in ("model.glb", "objects.json"):
        (job_dir / "out" / name).write_bytes((model / "out" / name).read_bytes())
    return job_dir


def provider(kind: str, base: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    if kind == "anthropic":
        monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
        return AnthropicProvider(base_url=base)
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    return OpenAIProvider(model="test-model", base_url=f"{base}/v1")


@pytest.mark.parametrize("kind", ["anthropic", "openai"])
def test_both_providers_meet_the_same_contract(
    kind: str, stub: tuple[str, Stub], job: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, state = stub
    svc = AiService({kind: provider(kind, base, monkeypatch)}, AiLimits(), prices={})
    result = ask(svc, job)
    assert result["operations"][0]["objectIds"] == ids_of(job, "E-001")
    assert result["operations"][0]["status"] == "ready"
    assert result["usage"] == {"inputTokens": 1000, "outputTokens": 200}
    [sent] = state.requests
    body = json.dumps(sent["body"])
    assert KEY not in body  # la clave solo en la cabecera
    assert "obj-" not in body and "431" not in body
    if kind == "anthropic":
        assert sent["path"] == "/v1/messages"
        headers = sent["headers"]
        assert KEY in (headers.get("x-api-key", ""), headers.get("authorization", "")[7:])
        assert headers["anthropic-version"]
        assert sent["body"]["output_config"]["format"]["type"] == "json_schema"
        assert sent["body"]["max_tokens"] == 2000
        assert sent["body"]["model"] == "claude-opus-5-5"
    else:
        assert sent["path"] == "/v1/responses"
        assert sent["headers"]["authorization"] == f"Bearer {KEY}"
        assert sent["body"]["text"]["format"]["strict"] is True
        assert sent["body"]["max_output_tokens"] == 2000
        assert sent["body"]["store"] is False
    ask(svc, job)  # idéntica: caché
    assert len(state.requests) == 1


@pytest.mark.parametrize("kind", ["anthropic", "openai"])
@pytest.mark.parametrize(
    ("mode", "error"), [("truncated", "truncated"), ("500", "provider"), ("401", "auth")]
)
def test_errors_and_truncation_make_a_single_request(
    kind: str,
    mode: str,
    error: str,
    stub: tuple[str, Stub],
    job: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, state = stub
    state.mode = mode
    svc = AiService({kind: provider(kind, base, monkeypatch)}, AiLimits(), prices={})
    with pytest.raises(AiError) as caught:
        ask(svc, job)
    assert caught.value.kind == error
    assert len(state.requests) == 1  # sin reintentos del SDK ni nuestros
    assert KEY not in caught.value.problem + caught.value.fix
