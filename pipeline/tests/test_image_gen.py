"""Generación de imágenes a partir de vistas: contrato HTTP con el proveedor simulado y
adaptadores reales (OpenAI, Gemini, Claude para sugerir el prompt) contra servidores locales
que imitan cada API. Sin red ni llamadas facturables."""

from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from test_server_async import call

from extrude.ai_providers import ProviderError
from extrude.image_gen import (
    FIDELITY_NOTE,
    GeminiImageProvider,
    ImageError,
    ImageService,
    OpenAIImageProvider,
    Reference,
    SimulatedImageProvider,
    encode_png,
    fit_box,
    image_info,
    suggest_prompt,
)
from extrude.server import create_server

KEY = "sk-test-SECRET-KEY"
PNG = encode_png(64, 36, bytes([180, 190, 200]) * (64 * 36))
REFERENCE = {"data": base64.b64encode(PNG).decode()}


def reference() -> Reference:
    return Reference("vista-1.png", "image/png", PNG, 64, 36)


def test_png_roundtrip_and_real_dimensions() -> None:
    assert image_info(PNG) == ("image/png", 64, 36)
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 11 + b"\xff\xc0\x00\x11\x08\x04\x38\x07\x80"
    assert image_info(jpeg + b"\x00" * 16) == ("image/jpeg", 1920, 1080)
    with pytest.raises(ImageError):
        image_info(b"no es una imagen")


def test_fit_box_keeps_the_view_aspect() -> None:
    assert fit_box(16 / 9, (1920, 1080)) == (1920, 1080)
    assert fit_box(4 / 3, (1920, 1080)) == (1440, 1080)
    assert fit_box(1, (3840, 2160)) == (2160, 2160)
    assert fit_box(21 / 9, (3840, 2160), 16) == (3840, 1648)


def test_plans_say_what_is_native_and_what_is_not() -> None:
    openai = OpenAIImageProvider()
    fixed = openai.plan("gpt-image-1", "4k", 16 / 9)
    assert (fixed.width, fixed.height) == (1536, 1024) and fixed.native is False
    assert "ampliar" in fixed.note
    assert openai.plan("gpt-image-1", "standard", 9 / 16).label == "1024x1536"
    flexible = openai.plan("gpt-image-2", "4k", 16 / 9)
    assert (flexible.width, flexible.height) == (3840, 2160) and flexible.native
    assert flexible.width % 16 == 0 and flexible.height % 16 == 0  # type: ignore[operator]
    hd = openai.plan("gpt-image-2", "hd", 16 / 9)
    assert (hd.width, hd.height) == (1920, 1088)  # múltiplos de 16

    gemini = GeminiImageProvider().plan("gemini-3.1-flash-image-preview", "4k", 16 / 9)
    assert gemini.params == {"aspectRatio": "16:9", "imageSize": "4K"} and gemini.width == 4096
    odd = GeminiImageProvider().plan("gemini-3.1-flash-image-preview", "hd", 1.9)
    assert odd.params["aspectRatio"] == "16:9" and "más próxima" in odd.note


# --- Flujo HTTP completo con el proveedor simulado ---------------------------------------------


@pytest.fixture
def server(tmp_path: Path) -> Iterator[str]:
    simulated = SimulatedImageProvider()
    simulated.delay_s = 0.2
    images = ImageService(tmp_path / "images", {"simulated": simulated})
    srv = create_server("127.0.0.1", 0, jobs_dir=tmp_path / "jobs", max_mb=5, images=images)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def post(url: str, body: Any) -> tuple[int, Any]:
    result: tuple[int, Any] = call(
        url, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    return result


def wait(base: str, status: str) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        code, job = call(base + status)
        assert code == 200, job
        if job["state"] not in ("pending", "running"):
            return dict(job)
        time.sleep(0.05)
    raise AssertionError("la generación no terminó")


def request(**changes: Any) -> dict[str, Any]:
    return {
        "provider": "simulated",
        "model": "prueba-local",
        "prompt": "Visualización fotorrealista con vegetación abundante",
        "target": "hd",
        "images": [REFERENCE],
        "requestId": "req-0001-abcdef",
        **changes,
    }


def test_providers_plan_generate_and_download(server: str, tmp_path: Path) -> None:
    code, info = call(f"{server}/api/images/providers")
    assert code == 200 and info["fidelityNote"] == FIDELITY_NOTE
    assert info["providers"][0]["simulated"] and info["providers"][0]["available"]
    assert KEY not in json.dumps(info)

    code, plan = post(
        f"{server}/api/images/plan", {"provider": "simulated", "width": 64, "height": 36}
    )
    assert code == 200 and [p["target"] for p in plan["plans"]] == ["standard", "hd", "4k"]
    assert (plan["plans"][2]["width"], plan["plans"][2]["height"]) == (3840, 2160)

    code, job = post(f"{server}/api/images/generate", request())
    assert code == 202 and job["state"] in ("pending", "running")
    done = wait(server, job["status"])
    assert done["state"] == "done", done
    result = done["result"]
    assert (result["width"], result["height"]) == (1920, 1080) and result["matchesRequest"]
    assert result["simulated"] is True and result["mime"] == "image/png"

    import urllib.request

    with urllib.request.urlopen(server + result["file"]) as resp:  # noqa: S310
        data = resp.read()
    assert image_info(data) == ("image/png", 1920, 1080)  # un archivo de imagen real
    # El registro no guarda el prompt ni las imágenes
    log = (tmp_path / "images" / "log.jsonl").read_text(encoding="utf-8")
    assert "fotorrealista" not in log and '"received": [1920, 1080]' in log


def test_double_click_does_not_duplicate_the_request(server: str, tmp_path: Path) -> None:
    first = post(f"{server}/api/images/generate", request())[1]
    second = post(f"{server}/api/images/generate", request())[1]
    assert first["id"] == second["id"]
    wait(server, first["status"])
    again = post(f"{server}/api/images/generate", request())[1]
    assert again["id"] == first["id"] and again["state"] == "done"  # reenvío: mismo resultado
    assert len((tmp_path / "images" / "log.jsonl").read_text().splitlines()) == 1
    # Un reintento del usuario es una solicitud nueva
    retry = post(f"{server}/api/images/generate", request(requestId="req-0002-abcdef"))[1]
    assert retry["id"] != first["id"]


def test_cancel_discards_the_result(server: str) -> None:
    job = post(f"{server}/api/images/generate", request(requestId="req-cancel-0001"))[1]
    code, cancelled = post(f"{server}/api/images/jobs/{job['id']}/cancel", {})
    assert code == 200 and cancelled["state"] == "cancelled"
    time.sleep(0.5)
    assert call(f"{server}/api/images/jobs/{job['id']}")[1]["state"] == "cancelled"
    assert call(f"{server}/api/images/jobs/{job['id']}/file")[0] == 404


@pytest.mark.parametrize(
    ("changes", "status", "kind"),
    [
        ({"prompt": "  "}, 400, "no_prompt"),
        ({"prompt": "x" * 4001}, 400, "too_long"),
        ({"images": []}, 400, "no_images"),
        ({"images": [REFERENCE] * 5}, 400, "too_many"),
        ({"images": [{"data": base64.b64encode(b"texto").decode()}]}, 400, "invalid"),
        ({"provider": "otro"}, 400, "unknown_provider"),
        ({"target": "8k"}, 400, "invalid"),
        ({"requestId": "x"}, 400, "invalid"),
    ],
)
def test_invalid_requests_never_reach_a_provider(
    server: str, changes: dict[str, Any], status: int, kind: str
) -> None:
    code, error = post(f"{server}/api/images/generate", request(**changes))
    assert (code, error["kind"]) == (status, kind)


def test_unknown_jobs_and_capacity(server: str) -> None:
    assert call(f"{server}/api/images/jobs/{'0' * 32}")[0] == 404
    codes = [
        post(f"{server}/api/images/generate", request(requestId=f"req-many-{i:04d}"))[0]
        for i in range(3)
    ]
    assert codes == [202, 202, 429]  # dos a la vez; la tercera se rechaza sin llamar


# --- Adaptadores reales contra servidores locales ------------------------------------------------


@dataclass
class Stub:
    mode: str = "ok"
    requests: list[dict[str, Any]] = field(default_factory=list)


@pytest.fixture
def provider_api() -> Iterator[tuple[str, Stub]]:
    stub = Stub()
    image = base64.b64encode(encode_png(48, 32, bytes(48 * 32 * 3))).decode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            stub.requests.append(
                {
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": raw,
                }
            )
            if stub.mode == "500":
                status, payload = 500, {"error": {"message": "boom", "type": "server_error"}}
            elif stub.mode == "400":
                status, payload = 400, {"error": {"message": "size", "code": "invalid_size"}}
            elif "images/edits" in self.path:
                status, payload = 200, {"created": 1, "data": [{"b64_json": image}]}
            elif "generateContent" in self.path:
                parts: list[dict[str, Any]] = [{"text": "aquí tienes"}]
                if stub.mode != "no_image":
                    parts.append({"inlineData": {"mimeType": "image/png", "data": image}})
                status, payload = 200, {"candidates": [{"content": {"parts": parts}}]}
            else:  # Anthropic Messages
                body = json.loads(raw)
                status, payload = (
                    200,
                    {
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "model": body["model"],
                        "content": [{"type": "text", "text": "  Prompt mejorado.  "}],
                        "stop_reason": "refusal" if stub.mode == "refusal" else "end_turn",
                        "stop_sequence": None,
                        "usage": {"input_tokens": 10, "output_tokens": 5},
                    },
                )
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", stub
    srv.shutdown()
    srv.server_close()


def test_openai_sends_reference_prompt_and_size_once(
    provider_api: tuple[str, Stub], monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("openai")
    base, stub = provider_api
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    provider = OpenAIImageProvider(base_url=f"{base}/v1")
    assert provider.available() is None
    plan = provider.plan("gpt-image-2", "hd", 16 / 9)
    out = provider.generate("gpt-image-2", "Fachadas de ladrillo claro", [reference()], plan)
    assert image_info(out.data) == ("image/png", 48, 32)  # lo recibido, no lo pedido
    sent = stub.requests[0]
    assert sent["headers"]["authorization"] == f"Bearer {KEY}"
    assert b"Fachadas de ladrillo claro" in sent["body"] and b"1920x1088" in sent["body"]
    assert PNG in sent["body"] and KEY.encode() not in sent["body"]

    for mode, kind in (("500", "provider"), ("400", "rejected")):
        stub.mode = mode
        before = len(stub.requests)
        with pytest.raises(ProviderError) as caught:
            provider.generate("gpt-image-2", "x", [reference()], plan)
        assert caught.value.kind == kind
        assert len(stub.requests) == before + 1  # sin reintentos automáticos
        assert "boom" not in caught.value.problem  # no se reenvía el cuerpo del proveedor


def test_gemini_sends_reference_and_image_config(
    provider_api: tuple[str, Stub], monkeypatch: pytest.MonkeyPatch
) -> None:
    base, stub = provider_api
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    provider = GeminiImageProvider(url=base + "/v1beta/models/{model}:generateContent")
    model = "gemini-3.1-flash-image-preview"
    plan = provider.plan(model, "4k", 16 / 9)
    out = provider.generate(model, "Luz de atardecer", [reference()], plan)
    assert image_info(out.data)[1:] == (48, 32)
    sent = stub.requests[0]
    body = json.loads(sent["body"])
    assert sent["headers"]["x-goog-api-key"] == KEY and KEY not in sent["path"]
    assert body["contents"][0]["parts"][0] == {"text": "Luz de atardecer"}
    assert body["contents"][0]["parts"][1]["inlineData"]["mimeType"] == "image/png"
    assert body["generationConfig"]["imageConfig"] == {"aspectRatio": "16:9", "imageSize": "4K"}

    stub.mode = "no_image"
    with pytest.raises(ProviderError) as caught:
        provider.generate(model, "x", [reference()], plan)
    assert caught.value.kind == "refused"
    stub.mode = "500"
    with pytest.raises(ProviderError) as caught:
        provider.generate(model, "x", [reference()], plan)
    assert caught.value.kind == "provider" and len(stub.requests) == 3


def test_service_reports_real_size_when_provider_returns_less(
    provider_api: tuple[str, Stub], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    base, _ = provider_api
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    provider = GeminiImageProvider(url=base + "/v1beta/models/{model}:generateContent")
    service = ImageService(tmp_path, {"gemini": provider})
    job = service.start(request(provider="gemini", model="", target="4k"), None)
    deadline = time.monotonic() + 10
    while service.get(job["id"], None).state in ("pending", "running"):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    done = service.get(job["id"], None).as_dict()
    assert done["requested"]["width"] == 4096
    assert (done["result"]["width"], done["result"]["height"]) == (48, 32)
    assert done["result"]["matchesRequest"] is False  # no es 4K nativo: se dice
    with pytest.raises(ImageError):
        service.get(job["id"], "otra-persona")  # los trabajos son de quien los pidió


def test_claude_suggests_a_prompt_from_the_views(
    provider_api: tuple[str, Stub], monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("anthropic")
    base, stub = provider_api
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ImageError) as missing:
        suggest_prompt("x", [reference()])
    assert missing.value.kind == "unavailable" and not stub.requests

    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.delenv("EXTRUDE_PROMPT_MODEL", raising=False)
    text = suggest_prompt("Ladrillo y árboles", [reference()], base_url=base)
    assert text == "Prompt mejorado."
    sent = stub.requests[0]
    body = json.loads(sent["body"])
    assert body["model"] == "claude-fable-5-2" and "thinking" not in body
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in sent["headers"]["anthropic-beta"]
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/png"
    assert "Ladrillo y árboles" in content[-1]["text"]
    headers = sent["headers"]
    assert KEY in (headers.get("x-api-key", ""), headers.get("authorization", "")[7:])
    assert KEY not in sent["body"].decode()

    stub.mode = "refusal"
    with pytest.raises(ImageError) as refused:
        suggest_prompt("x", [reference()], base_url=base)
    assert refused.value.kind == "refused"
    stub.mode = "500"
    before = len(stub.requests)
    with pytest.raises(ImageError):
        suggest_prompt("x", [reference()], base_url=base)
    assert len(stub.requests) == before + 1
