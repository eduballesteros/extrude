"""Generación de imágenes arquitectónicas a partir de capturas de vistas del visor.

- La entrada es una o varias capturas (imágenes de referencia) y el prompt libre del usuario,
  que es la instrucción principal: el servidor no lo sustituye ni añade operaciones propias.
- Las claves solo existen en el servidor (ajustes guardados o entorno) y solo viajan en la
  cabecera de la petición al proveedor. Una generación = una llamada, sin reintentos
  automáticos (los inicia el usuario). `requestId` hace idempotente el envío: un doble clic o
  un reenvío con el mismo identificador devuelve el mismo trabajo, no otra llamada de pago.
- El resultado es el archivo que entrega el proveedor, sin tocar. Sus dimensiones reales las
  lee el servidor del propio archivo (nunca se confía en las pedidas).
- Ningún proveedor garantiza conservar la geometría: se avisa en `fidelityNote`.
- `SimulatedImageProvider` no usa IA ni la referencia: genera un PNG de prueba para recorrer
  el flujo completo sin red ni coste, y así se identifica en el visor.
- `suggest_prompt` usa Claude (visión) para proponer una redacción del prompt; no genera
  imágenes y su resultado solo se usa si el usuario lo acepta.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import struct
import threading
import time
import urllib.error
import urllib.request
import uuid
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from extrude.ai_providers import ProviderError
from extrude.errors import ExtrudeError

MAX_PROMPT_CHARS = 4000
MAX_REFERENCES = 4
MAX_REFERENCE_BYTES = 12 * 1024 * 1024
MAX_RESULT_BYTES = 80 * 1024 * 1024
KEEP_JOBS = 30
TIMEOUT_S = 300
TARGETS = ("standard", "hd", "4k")
HD = (1920, 1080)
UHD = (3840, 2160)
MIME_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
FIDELITY_NOTE = (
    "El proveedor crea una imagen nueva a partir de la referencia: procura conservar el "
    "encuadre y los volúmenes, pero no lo garantiza. Compárala siempre con la captura original."
)
PROMPT_MODEL_DEFAULT = "claude-fable-5-2"
# Modelos de Claude que admiten `fallbacks` del servidor ante un rechazo de seguridad
FALLBACK_MODELS = (
    "claude-fable-5-2",
    "claude-fable-5-1",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5-5",
)


class ImageError(ExtrudeError):
    """Error con estado HTTP y un `kind` estable para el visor."""

    def __init__(self, status: int, kind: str, problem: str, fix: str) -> None:
        super().__init__(problem, fix)
        self.status = status
        self.kind = kind


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


# --- Imágenes: tipo y dimensiones reales ------------------------------------------------------


def image_info(data: bytes) -> tuple[str, int, int]:
    """(mime, ancho, alto) leídos de la cabecera del archivo (PNG, JPEG o WebP)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        return "image/png", width, height
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB):
                height, width = struct.unpack(">HH", data[i + 5 : i + 9])
                return "image/jpeg", width, height
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            i += 2 + struct.unpack(">H", data[i + 2 : i + 4])[0]
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X":
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
            return "image/webp", width, height
        if chunk == b"VP8 ":
            width, height = struct.unpack("<HH", data[26:30])
            return "image/webp", width & 0x3FFF, height & 0x3FFF
        if chunk == b"VP8L":
            bits = int.from_bytes(data[21:25], "little")
            return "image/webp", (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    raise ImageError(
        502, "invalid", "El archivo recibido no es una imagen PNG, JPEG o WebP.", "Reintenta."
    )


def encode_png(width: int, height: int, rgb: bytes) -> bytes:
    """PNG RGB de 8 bits sin dependencias (zlib de la librería estándar)."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride : (y + 1) * stride] for y in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


@dataclass(frozen=True)
class Reference:
    """Captura de una vista enviada como referencia."""

    name: str
    mime: str
    data: bytes
    width: int
    height: int


@dataclass(frozen=True)
class SizePlan:
    """Qué se pedirá al proveedor para un objetivo (estándar, HD o 4K) y qué cabe esperar."""

    target: str
    label: str  # lo que se pide, legible: «1536x1024», «16:9 · 4K»…
    width: int | None  # dimensiones esperadas (None si el proveedor no las fija)
    height: int | None
    native: bool  # el proveedor genera de forma nativa al menos la resolución del objetivo
    note: str
    params: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "label": self.label,
            "width": self.width,
            "height": self.height,
            "native": self.native,
            "note": self.note,
        }


@dataclass(frozen=True)
class Generated:
    data: bytes
    model: str


def fit_box(aspect: float, box: tuple[int, int], multiple: int = 1) -> tuple[int, int]:
    """Mayor tamaño de relación `aspect` dentro de `box`, redondeado a múltiplos de `multiple`."""
    width, height = box
    if aspect >= width / height:
        w, h = float(width), width / aspect
    else:
        w, h = height * aspect, float(height)

    def snap(value: float) -> int:
        return max(multiple, round(value / multiple) * multiple)

    return snap(w), snap(h)


def _target_box(target: str) -> tuple[int, int]:
    return UHD if target == "4k" else HD


def _reaches(width: int, height: int, target: str, aspect: float) -> bool:
    want = fit_box(aspect, _target_box(target))
    return width >= want[0] * 0.98 and height >= want[1] * 0.98


class ImageProvider(Protocol):
    id: str
    name: str
    simulated: bool

    def models(self) -> list[str]: ...

    def available(self) -> str | None:
        """None si está listo; si no, el motivo (sin revelar secretos)."""

    def plan(self, model: str, target: str, aspect: float) -> SizePlan: ...

    def generate(
        self, model: str, prompt: str, references: list[Reference], plan: SizePlan
    ) -> Generated: ...


# --- OpenAI (Images API, edición con imágenes de referencia) -----------------------------------

OPENAI_FIXED_SIZES = ((1024, 1024), (1536, 1024), (1024, 1536))
OPENAI_FLEXIBLE_PREFIXES = ("gpt-image-2",)
OPENAI_IMAGE_MODELS = ("gpt-image-2", "gpt-image-1.5", "gpt-image-1")


class OpenAIImageProvider:
    """`client.images.edit` del SDK oficial: acepta imágenes de referencia."""

    id = "openai"
    name = "OpenAI (GPT Image)"
    simulated = False

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or _env("EXTRUDE_OPENAI_BASE_URL") or None

    def models(self) -> list[str]:
        configured = _env("EXTRUDE_OPENAI_IMAGE_MODEL")
        return list(
            dict.fromkeys([configured, *OPENAI_IMAGE_MODELS] if configured else OPENAI_IMAGE_MODELS)
        )

    def available(self) -> str | None:
        if not _env("OPENAI_API_KEY"):
            return "Falta OPENAI_API_KEY en el servidor."
        try:
            import openai  # noqa: F401
        except ImportError:
            return "Falta el SDK de OpenAI en el servidor (uv sync --extra ai)."
        return None

    def plan(self, model: str, target: str, aspect: float) -> SizePlan:
        if model.startswith(OPENAI_FLEXIBLE_PREFIXES):
            # Tamaños libres: lados múltiplos de 16 y relación entre 1:3 y 3:1 (documentación
            # de la API). Los límites de píxeles dependen del modelo: si no admite el tamaño,
            # el proveedor lo rechaza y se muestra su error.
            clamped = min(max(aspect, 1 / 3), 3.0)
            box = (1536, 1024) if target == "standard" else _target_box(target)
            if target == "standard" and clamped < 1:
                box = (1024, 1536)
            width, height = fit_box(clamped, box, 16)
            note = "Tamaño nativo pedido al modelo (lados múltiplos de 16)."
            if target == "4k":
                note += (
                    " Si el modelo no admite esta resolución, el proveedor rechazará la petición."
                )
            if clamped != aspect:
                note += " La relación de aspecto se ha limitado al rango 1:3 a 3:1."
            return SizePlan(
                target,
                f"{width}x{height}",
                width,
                height,
                True,
                note,
                {"size": f"{width}x{height}"},
            )
        width, height = min(OPENAI_FIXED_SIZES, key=lambda s: abs(math.log((s[0] / s[1]) / aspect)))
        native = target == "standard" or _reaches(width, height, target, aspect)
        note = f"{model} solo genera 1024x1024, 1536x1024 o 1024x1536."
        if not native:
            note += (
                f" No alcanza {'4K' if target == '4k' else 'HD'} nativo: se pedirá {width}x{height}"
                " y podrás ampliarla después (quedará marcada como ampliada)."
            )
        return SizePlan(
            target, f"{width}x{height}", width, height, native, note, {"size": f"{width}x{height}"}
        )

    def generate(
        self, model: str, prompt: str, references: list[Reference], plan: SizePlan
    ) -> Generated:
        import openai

        client = openai.OpenAI(
            api_key=_env("OPENAI_API_KEY"), max_retries=0, timeout=TIMEOUT_S, base_url=self.base_url
        )
        images = [(r.name, r.data, r.mime) for r in references]
        try:
            response = client.images.edit(
                model=model, image=images, prompt=prompt, size=plan.params["size"], n=1
            )
        except openai.APITimeoutError as exc:
            raise ProviderError("timeout", f"{self.name} no respondió a tiempo.", RETRY) from exc
        except openai.APIConnectionError as exc:
            raise ProviderError("network", f"No se pudo conectar con {self.name}.", RETRY) from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise ProviderError(
                "auth", f"{self.name} rechazó la clave o sus permisos.", "Revisa OPENAI_API_KEY."
            ) from exc
        except openai.RateLimitError as exc:
            raise ProviderError(
                "quota", f"{self.name} indica límite o cuota agotada.", RETRY
            ) from exc
        except openai.NotFoundError as exc:
            raise ProviderError(
                "model", f"{self.name} no reconoce el modelo «{model}».", "Elige otro modelo."
            ) from exc
        except openai.BadRequestError as exc:
            # Tamaño no admitido, contenido rechazado…: el motivo lo da el proveedor en `code`
            code = getattr(exc, "code", None) or "petición no válida"
            raise ProviderError(
                "rejected",
                f"{self.name} rechazó la petición ({code}).",
                "Prueba otra resolución, otro modelo o reformula el prompt.",
            ) from exc
        except openai.APIStatusError as exc:
            raise ProviderError(
                "provider", f"{self.name} devolvió un error HTTP {exc.status_code}.", RETRY
            ) from exc
        item = response.data[0] if response.data else None
        encoded = getattr(item, "b64_json", None)
        if not encoded:
            raise ProviderError("invalid", f"{self.name} no devolvió ninguna imagen.", RETRY)
        return Generated(base64.b64decode(encoded), model)


RETRY = "No se reintenta solo: vuelve a pulsar Generar si quieres repetirlo."

# --- Google Gemini (generateContent con imagen de referencia) ----------------------------------

GEMINI_IMAGE_MODELS = ("gemini-3.1-flash-image-preview", "gemini-3-pro-image-preview")
GEMINI_ASPECTS = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9")
GEMINI_TIERS = {"1K": 1024, "2K": 2048, "4K": 4096}
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _ratio(text: str) -> float:
    a, b = text.split(":")
    return int(a) / int(b)


class GeminiImageProvider:
    """Gemini («Nano Banana») por REST: admite imágenes de referencia y tamaños 1K/2K/4K."""

    id = "gemini"
    name = "Google Gemini (imagen)"
    simulated = False

    def __init__(self, url: str | None = None) -> None:
        self.url = url or _env("EXTRUDE_GEMINI_URL") or GEMINI_URL

    def models(self) -> list[str]:
        configured = _env("EXTRUDE_GEMINI_IMAGE_MODEL")
        return list(
            dict.fromkeys([configured, *GEMINI_IMAGE_MODELS] if configured else GEMINI_IMAGE_MODELS)
        )

    def available(self) -> str | None:
        return None if _env("GEMINI_API_KEY") else "Falta GEMINI_API_KEY en el servidor."

    def plan(self, model: str, target: str, aspect: float) -> SizePlan:
        ratio = min(GEMINI_ASPECTS, key=lambda r: abs(math.log(_ratio(r) / aspect)))
        tier = {"standard": "1K", "hd": "2K", "4k": "4K"}[target]
        long_side = GEMINI_TIERS[tier]
        value = _ratio(ratio)
        width = long_side if value >= 1 else round(long_side * value)
        height = round(long_side / value) if value >= 1 else long_side
        note = (
            f"Se pide relación {ratio} y tamaño {tier} (lado mayor ≈ {long_side} px). Las "
            "dimensiones exactas las decide el modelo: se mostrarán las reales al recibirla."
        )
        if abs(math.log(value / aspect)) > 0.02:
            note += " La relación de la vista no está entre las admitidas: se usa la más próxima."
        return SizePlan(
            target,
            f"{ratio} · {tier}",
            width,
            height,
            True,
            note,
            {"aspectRatio": ratio, "imageSize": tier},
        )

    def generate(
        self, model: str, prompt: str, references: list[Reference], plan: SizePlan
    ) -> Generated:
        parts: list[dict[str, Any]] = [{"text": prompt}]
        parts += [
            {"inlineData": {"mimeType": r.mime, "data": base64.b64encode(r.data).decode("ascii")}}
            for r in references
        ]
        body = json.dumps(
            {
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {
                    "responseModalities": ["TEXT", "IMAGE"],
                    "imageConfig": dict(plan.params),
                },
            }
        ).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 (URL https fija o de configuración)
            self.url.format(model=model),
            data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": _env("GEMINI_API_KEY")},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
                payload = json.loads(response.read(MAX_RESULT_BYTES * 2))
        except urllib.error.HTTPError as exc:
            status = exc.code
            if status in (401, 403):
                raise ProviderError(
                    "auth", f"{self.name} rechazó la clave.", "Revisa GEMINI_API_KEY."
                ) from exc
            if status == 404:
                raise ProviderError(
                    "model", f"{self.name} no reconoce el modelo «{model}».", "Elige otro modelo."
                ) from exc
            if status == 429:
                raise ProviderError(
                    "quota", f"{self.name} indica límite o cuota agotada.", RETRY
                ) from exc
            if status == 400:
                raise ProviderError(
                    "rejected",
                    f"{self.name} rechazó la petición.",
                    "Prueba otra resolución, otro modelo o reformula el prompt.",
                ) from exc
            raise ProviderError(
                "provider", f"{self.name} devolvió un error HTTP {status}.", RETRY
            ) from exc
        except TimeoutError as exc:
            raise ProviderError("timeout", f"{self.name} no respondió a tiempo.", RETRY) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ProviderError("network", f"No se pudo conectar con {self.name}.", RETRY) from exc
        except ValueError as exc:
            raise ProviderError(
                "invalid", f"{self.name} devolvió una respuesta ilegible.", RETRY
            ) from exc
        for candidate in payload.get("candidates") or []:
            for part in (candidate.get("content") or {}).get("parts") or []:
                inline = part.get("inlineData") or part.get("inline_data")
                if isinstance(inline, dict) and inline.get("data"):
                    try:
                        return Generated(base64.b64decode(inline["data"]), model)
                    except (binascii.Error, ValueError) as exc:
                        raise ProviderError(
                            "invalid", f"{self.name} devolvió datos corruptos.", RETRY
                        ) from exc
        raise ProviderError(
            "refused",
            f"{self.name} no devolvió ninguna imagen (pudo rechazar el contenido).",
            "Reformula el prompt o usa otra vista.",
        )


# --- Simulado ------------------------------------------------------------------------------------


class SimulatedImageProvider:
    """Imagen de prueba local (degradado según el prompt). No es IA ni usa la referencia."""

    id = "simulated"
    name = "Simulado (imagen de prueba, sin IA)"
    simulated = True
    delay_s = 0.6

    def models(self) -> list[str]:
        return ["prueba-local"]

    def available(self) -> str | None:
        return None

    def plan(self, model: str, target: str, aspect: float) -> SizePlan:
        box = (1536, 1024) if target == "standard" else _target_box(target)
        width, height = fit_box(aspect, box, 2)
        return SizePlan(
            target,
            f"{width}x{height}",
            width,
            height,
            True,
            "Imagen de prueba generada en el servidor: no usa IA ni la captura de referencia.",
            {"size": (width, height)},
        )

    def generate(
        self, model: str, prompt: str, references: list[Reference], plan: SizePlan
    ) -> Generated:
        import numpy as np

        time.sleep(self.delay_s)
        width, height = plan.params["size"]
        seed = hashlib.sha256(prompt.encode("utf-8")).digest()
        top = np.array([120 + seed[0] % 100, 150 + seed[1] % 90, 200 + seed[2] % 55], dtype=float)
        bottom = np.array([200 + seed[3] % 55, 170 + seed[4] % 60, 110 + seed[5] % 80], dtype=float)
        t = np.linspace(0.0, 1.0, height)[:, None, None]
        image = np.broadcast_to(top * (1 - t) + bottom * t, (height, width, 3)).copy()
        # Banda diagonal: deja claro a simple vista que es una imagen de prueba
        xs, ys = np.meshgrid(np.arange(width), np.arange(height))
        band = ((xs + ys) // max(width // 24, 8)) % 2 == 0
        image[band] *= 0.93
        return Generated(encode_png(width, height, image.astype(np.uint8).tobytes()), model)


def default_image_providers(simulated: bool = False) -> dict[str, ImageProvider]:
    providers: dict[str, ImageProvider] = {
        "openai": OpenAIImageProvider(),
        "gemini": GeminiImageProvider(),
    }
    if simulated or _env("EXTRUDE_AI_SIMULATED") in ("1", "true", "yes"):
        providers = {"simulated": SimulatedImageProvider(), **providers}
    return providers


# --- Sugerencia de prompt con Claude ------------------------------------------------------------

SUGGEST_SYSTEM = """\
Eres director de visualización arquitectónica, con años de experiencia en renders de concurso \
y fotografía de arquitectura, y experto en redactar prompts para generadores de imágenes. \
Ayudas a un arquitecto a redactar el prompt para un generador que recibirá como referencia las \
mismas capturas de un modelo 3D de volúmenes que ves tú. El generador debe convertir la \
captura en una visualización arquitectónica fotorrealista y creíble.

Recibes las capturas y el borrador del usuario (puede estar vacío). Devuelve únicamente el \
prompt mejorado, en el idioma del borrador (español si está vacío), listo para pegar: sin \
saludos, explicaciones, comillas ni formato Markdown.

El prompt debe:
- Empezar con una frase de rol para el generador (p. ej. «Actúa como visualizador \
arquitectónico experto en renders fotorrealistas»); si el borrador ya empieza con un rol, \
conserva ese.
- Conservar la intención y cada petición concreta del borrador; no inventes un programa ni \
un estilo que el usuario no haya sugerido, salvo para concretar lo que dejó abierto.
- Describir lo que se ve en la captura (punto de vista, número y disposición de volúmenes, \
alturas relativas, viales, terreno) para que el generador lo mantenga.
- Pedir expresamente que se conserven el encuadre, la cámara, la volumetría, la distribución \
y las relaciones espaciales, y que no se añadan ni quiten edificios.
- Pedir edificios completos y verosímiles: ventanas y carpinterías en todas las fachadas y \
plantas, plantas bajas resueltas, cubiertas y remates acabados; nada de volúmenes lisos.
- Concretar materiales, vegetación, luz, hora, clima, atmósfera y entorno de forma coherente \
con el borrador, y una escala humana realista (personas y vida urbana proporcionadas) si \
encaja con la escena.
- Ocupar un solo párrafo de 100 a 180 palabras."""


def suggest_prompt(prompt: str, references: list[Reference], *, base_url: str | None = None) -> str:
    """Propuesta de prompt de Claude a partir de las capturas (una llamada, sin reintentos)."""
    key = _env("ANTHROPIC_API_KEY")
    if not key:
        raise ImageError(
            503,
            "unavailable",
            "La ayuda de Claude no está disponible.",
            "Falta ANTHROPIC_API_KEY en el servidor.",
        )
    try:
        import anthropic
    except ImportError as exc:
        raise ImageError(
            503,
            "unavailable",
            "La ayuda de Claude no está disponible.",
            "Falta el SDK de Anthropic en el servidor (uv sync --extra ai).",
        ) from exc
    model = _env("EXTRUDE_PROMPT_MODEL") or PROMPT_MODEL_DEFAULT
    client = anthropic.Anthropic(
        api_key=key,
        max_retries=0,
        timeout=120,
        base_url=base_url or _env("EXTRUDE_ANTHROPIC_BASE_URL") or None,
    )
    content: list[dict[str, Any]] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": r.mime,
                "data": base64.standard_b64encode(r.data).decode("ascii"),
            },
        }
        for r in references
    ]
    draft = prompt.strip() or "(vacío)"
    content.append({"type": "text", "text": f"Borrador del usuario:\n{draft}"})
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": 4000,
        "system": SUGGEST_SYSTEM,
        "messages": [{"role": "user", "content": content}],
        "output_config": {"effort": "low"},
    }
    name = "Claude"
    try:
        if model in FALLBACK_MODELS:
            # Ante un rechazo de los clasificadores de seguridad, el servidor de Anthropic
            # repite la petición en el modelo de respaldo dentro de la misma llamada.
            response = client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **request
            )
        else:
            response = client.messages.create(**request)
    except anthropic.APITimeoutError as exc:
        raise ImageError(504, "timeout", f"{name} no respondió a tiempo.", RETRY) from exc
    except anthropic.APIConnectionError as exc:
        raise ImageError(502, "network", f"No se pudo conectar con {name}.", RETRY) from exc
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        raise ImageError(
            502, "auth", f"{name} rechazó la clave o sus permisos.", "Revisa ANTHROPIC_API_KEY."
        ) from exc
    except anthropic.RateLimitError as exc:
        raise ImageError(429, "quota", f"{name} indica límite o cuota agotada.", RETRY) from exc
    except anthropic.NotFoundError as exc:
        raise ImageError(
            502, "model", f"{name} no reconoce el modelo «{model}».", "Revisa EXTRUDE_PROMPT_MODEL."
        ) from exc
    except anthropic.APIStatusError as exc:
        raise ImageError(
            502, "provider", f"{name} devolvió un error HTTP {exc.status_code}.", RETRY
        ) from exc
    if response.stop_reason == "refusal":
        raise ImageError(
            422,
            "refused",
            f"{name} no ha querido analizar estas vistas.",
            "Escribe el prompt a mano.",
        )
    if response.stop_reason == "max_tokens":
        raise ImageError(502, "truncated", f"{name} no terminó la sugerencia.", RETRY)
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise ImageError(502, "invalid", f"{name} no devolvió ninguna sugerencia.", RETRY)
    return text[:MAX_PROMPT_CHARS]


# --- Servicio ------------------------------------------------------------------------------------


@dataclass
class ImageJob:
    id: str
    owner: str | None
    request_id: str
    provider: str
    model: str
    plan: SizePlan
    prompt_chars: int
    references: int
    created: float = field(default_factory=time.time)
    state: str = "pending"  # pending | running | done | failed | cancelled
    error: str | None = None
    fix: str | None = None
    kind: str | None = None
    result: dict[str, Any] | None = None
    path: Path | None = None
    cancelled: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "state": self.state,
            "provider": self.provider,
            "model": self.model,
            "requested": self.plan.as_dict(),
            "status": f"/api/images/jobs/{self.id}",
        }
        if self.result is not None:
            payload["result"] = self.result
        if self.error is not None:
            payload.update(error=self.error, fix=self.fix, kind=self.kind)
        return payload


def parse_references(raw: object) -> list[Reference]:
    if not isinstance(raw, list) or not raw:
        raise ImageError(
            400, "no_images", "Elige al menos una vista de origen.", "Marca una vista."
        )
    if len(raw) > MAX_REFERENCES:
        raise ImageError(
            400, "too_many", f"Como máximo {MAX_REFERENCES} vistas por generación.", "Quita alguna."
        )
    references: list[Reference] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or not isinstance(item.get("data"), str):
            raise ImageError(
                400, "invalid", "Imagen de referencia no válida.", "Vuelve a capturarla."
            )
        try:
            data = base64.b64decode(item["data"], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImageError(
                400, "invalid", "Imagen de referencia no válida.", "Vuelve a capturarla."
            ) from exc
        if len(data) > MAX_REFERENCE_BYTES:
            raise ImageError(
                413, "too_large", "Una imagen de referencia supera 12 MB.", "Captúrala en HD."
            )
        try:
            mime, width, height = image_info(data)
        except ImageError as exc:
            raise ImageError(
                400, "invalid", "La referencia no es PNG, JPEG ni WebP.", "Vuelve a capturarla."
            ) from exc
        ext = MIME_EXT[mime]
        references.append(Reference(f"vista-{index + 1}.{ext}", mime, data, width, height))
    return references


class ImageService:
    """Trabajos de generación: una llamada por trabajo, en un hilo; estado consultable."""

    def __init__(
        self,
        root: Path,
        providers: dict[str, ImageProvider] | None = None,
        max_running: int = 2,
    ) -> None:
        self.root = root
        self.providers = providers if providers is not None else default_image_providers()
        self.max_running = max_running
        self._jobs: dict[str, ImageJob] = {}
        self._by_request: dict[tuple[str | None, str], str] = {}
        self._lock = threading.Lock()

    # --- consultas sin coste -------------------------------------------------------------
    def providers_payload(self) -> dict[str, Any]:
        entries = []
        for provider in self.providers.values():
            reason = provider.available()
            entries.append(
                {
                    "id": provider.id,
                    "name": provider.name,
                    "models": provider.models(),
                    "simulated": provider.simulated,
                    "available": reason is None,
                    "reason": reason,
                }
            )
        assist = None if _env("ANTHROPIC_API_KEY") else "Falta ANTHROPIC_API_KEY en el servidor."
        return {
            "providers": entries,
            "limits": {
                "maxPromptChars": MAX_PROMPT_CHARS,
                "maxReferences": MAX_REFERENCES,
                "maxReferenceBytes": MAX_REFERENCE_BYTES,
            },
            "targets": list(TARGETS),
            "fidelityNote": FIDELITY_NOTE,
            "promptAssist": {
                "available": assist is None,
                "reason": assist,
                "model": _env("EXTRUDE_PROMPT_MODEL") or PROMPT_MODEL_DEFAULT,
            },
        }

    def _provider(self, provider_id: object, model: object) -> tuple[ImageProvider, str]:
        provider = self.providers.get(provider_id) if isinstance(provider_id, str) else None
        if provider is None:
            raise ImageError(
                400, "unknown_provider", "Proveedor de imágenes desconocido.", "Elige otro."
            )
        reason = provider.available()
        if reason:
            raise ImageError(503, "unavailable", f"{provider.name} no está disponible.", reason)
        name = model.strip() if isinstance(model, str) else ""
        if not name:
            name = provider.models()[0]
        if len(name) > 100 or not all(c.isalnum() or c in "._:/@-" for c in name):
            raise ImageError(400, "model", "Nombre de modelo no válido.", "Elige uno de la lista.")
        return provider, name

    def plan(self, body: dict[str, Any]) -> dict[str, Any]:
        provider, model = self._provider(body.get("provider"), body.get("model"))
        return {
            "provider": provider.id,
            "model": model,
            "plans": [
                provider.plan(model, target, self._aspect(body)).as_dict() for target in TARGETS
            ],
        }

    @staticmethod
    def _aspect(body: dict[str, Any]) -> float:
        width, height = body.get("width"), body.get("height")
        ok = all(
            isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) and v > 0
            for v in (width, height)
        )
        if not ok:
            raise ImageError(
                400, "invalid", "Dimensiones de la vista no válidas.", "Vuelve a capturar."
            )
        return float(width) / float(height)  # type: ignore[arg-type]

    # --- generación ----------------------------------------------------------------------
    def start(self, body: dict[str, Any], owner: str | None) -> dict[str, Any]:
        request_id = body.get("requestId")
        if not isinstance(request_id, str) or not (8 <= len(request_id) <= 64):
            raise ImageError(400, "invalid", "Falta el identificador de la solicitud.", "Recarga.")
        with self._lock:
            known = self._by_request.get((owner, request_id))
            if known and known in self._jobs:
                return self._jobs[known].as_dict()  # doble clic o reenvío: mismo trabajo
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ImageError(
                400, "no_prompt", "Escribe el prompt.", "Describe la imagen que quieres."
            )
        if len(prompt) > MAX_PROMPT_CHARS:
            raise ImageError(
                400, "too_long", f"El prompt supera {MAX_PROMPT_CHARS} caracteres.", "Acórtalo."
            )
        provider, model = self._provider(body.get("provider"), body.get("model"))
        target = body.get("target")
        if target not in TARGETS:
            raise ImageError(400, "invalid", "Resolución no válida.", "Elige estándar, HD o 4K.")
        references = parse_references(body.get("images"))
        first = references[0]
        plan = provider.plan(model, target, first.width / first.height)
        job = ImageJob(
            uuid.uuid4().hex,
            owner,
            request_id,
            provider.id,
            model,
            plan,
            len(prompt),
            len(references),
        )
        with self._lock:
            known = self._by_request.get((owner, request_id))
            if known and known in self._jobs:
                return self._jobs[known].as_dict()
            running = sum(j.state in ("pending", "running") for j in self._jobs.values())
            if running >= self.max_running:
                raise ImageError(
                    429,
                    "busy",
                    "Ya hay generaciones en curso en el servidor.",
                    "Espera a que terminen y vuelve a pulsar Generar.",
                )
            self._jobs[job.id] = job
            self._by_request[(owner, request_id)] = job.id
        threading.Thread(
            target=self._run,
            args=(job, provider, prompt, references),
            daemon=True,
            name=f"extrude-image-{job.id[:8]}",
        ).start()
        return job.as_dict()

    def _run(
        self, job: ImageJob, provider: ImageProvider, prompt: str, references: list[Reference]
    ) -> None:
        job.state = "running"
        started = time.monotonic()
        try:
            generated = provider.generate(job.model, prompt, references, job.plan)
            if len(generated.data) > MAX_RESULT_BYTES:
                raise ProviderError("invalid", "La imagen recibida es demasiado grande.", RETRY)
            mime, width, height = image_info(generated.data)
            if job.cancelled:
                job.state = "cancelled"
                return
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / f"{job.id}.{MIME_EXT[mime]}"
            path.write_bytes(generated.data)
            job.path = path
            job.result = {
                "mime": mime,
                "width": width,
                "height": height,
                "bytes": len(generated.data),
                "model": generated.model,
                "file": f"/api/images/jobs/{job.id}/file",
                "simulated": provider.simulated,
                # Nativo solo si lo recibido alcanza de verdad la resolución pedida
                "matchesRequest": job.plan.width is None
                or (width >= job.plan.width * 0.98 and height >= (job.plan.height or 0) * 0.98),
            }
            job.state = "done"
        except ProviderError as exc:
            job.error, job.fix, job.kind = exc.problem, exc.fix, exc.kind
            job.state = "cancelled" if job.cancelled else "failed"
        except ImageError as exc:
            job.error, job.fix, job.kind = exc.problem, exc.fix, exc.kind
            job.state = "cancelled" if job.cancelled else "failed"
        except Exception as exc:  # el hilo no debe morir en silencio: el visor espera un estado
            print(f"[extrude serve] Error inesperado generando imagen: {exc!r}", flush=True)
            job.error, job.fix, job.kind = (
                "Error inesperado al generar la imagen.",
                RETRY,
                "internal",
            )
            job.state = "failed"
        finally:
            self._log(job, time.monotonic() - started)
            self._prune()

    def _log(self, job: ImageJob, seconds: float) -> None:
        """Registro sin prompt ni imágenes: proveedor, modelo, estado y dimensiones."""
        entry = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "provider": job.provider,
            "model": job.model,
            "state": job.state,
            "kind": job.kind,
            "requested": job.plan.label,
            "received": [job.result["width"], job.result["height"]] if job.result else None,
            "references": job.references,
            "promptChars": job.prompt_chars,
            "seconds": round(seconds, 2),
        }
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / "log.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _prune(self) -> None:
        with self._lock:
            finished = sorted(
                (j for j in self._jobs.values() if j.state not in ("pending", "running")),
                key=lambda j: j.created,
            )
            for old in finished[: max(len(finished) - KEEP_JOBS, 0)]:
                self._jobs.pop(old.id, None)
                self._by_request.pop((old.owner, old.request_id), None)
                if old.path is not None:
                    old.path.unlink(missing_ok=True)

    def get(self, job_id: str, owner: str | None) -> ImageJob:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None or job.owner != owner:
            raise ImageError(
                404,
                "not_found",
                "Esa generación ya no está en el servidor.",
                "Si no llegó a guardarse en tu biblioteca, vuelve a generarla.",
            )
        return job

    def cancel(self, job_id: str, owner: str | None) -> dict[str, Any]:
        """Deja de esperar y descarta el resultado. El proveedor puede terminar (y facturar)."""
        job = self.get(job_id, owner)
        if job.state in ("pending", "running"):
            job.cancelled = True
            job.state = "cancelled"
        return job.as_dict()

    def file(self, job_id: str, owner: str | None) -> tuple[bytes, str]:
        job = self.get(job_id, owner)
        if job.state != "done" or job.path is None or not job.path.is_file() or job.result is None:
            raise ImageError(
                404, "not_found", "La imagen no está disponible.", "Vuelve a generarla."
            )
        return job.path.read_bytes(), str(job.result["mime"])
