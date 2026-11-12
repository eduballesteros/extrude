"""Adaptadores de IA con una interfaz común (docs/REQUISITOS_MATERIALIDAD_IA.md §11).

- Una llamada por invocación: los SDK se crean con `max_retries=0` (sin reintentos facturables
  automáticos) y un tiempo límite explícito.
- Respuesta estructurada con el mecanismo de cada proveedor (Anthropic: `output_config.format`;
  OpenAI: Responses API con `text.format` json_schema estricto). El resultado se valida igualmente
  en el servidor (ai_planner.py): el esquema del proveedor no sustituye la validación propia.
- Los errores se traducen a `ProviderError` con un tipo estable (auth, quota, network, timeout,
  truncated, refused, provider). Nunca se reenvía el cuerpo de error del proveedor: puede contener
  partes del prompt.
- Las claves se leen del entorno en el servidor y solo viajan en la cabecera de la petición.
- `SimulatedProvider` interpreta la descripción con reglas locales, sin red: sirve para desarrollar
  y probar el flujo sin llamadas facturables. No es IA y así se muestra en el visor.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Protocol

ANTHROPIC_DEFAULT_MODEL = "claude-opus-5-5"
ANTHROPIC_DEFAULT_EFFORT = "low"

# Límites conocidos (tokens). Para modelos no listados el límite no se puede verificar y se dice.
KNOWN_LIMITS: dict[str, tuple[int, int]] = {
    "claude-fable-5-2": (1_000_000, 128_000),
    "claude-opus-5-5": (1_000_000, 128_000),
    "claude-opus-5": (1_000_000, 128_000),
    "claude-sonnet-5-5": (1_000_000, 128_000),
    "claude-sonnet-5": (1_000_000, 128_000),
    "claude-haiku-5-5": (1_000_000, 128_000),
    "claude-haiku-4-5": (200_000, 64_000),
}


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None
    output_tokens: int | None

    def as_dict(self) -> dict[str, int | None]:
        return {"inputTokens": self.input_tokens, "outputTokens": self.output_tokens}


@dataclass(frozen=True)
class Completion:
    text: str
    usage: Usage
    model: str


class ProviderError(Exception):
    """Fallo de una llamada. `kind` es estable para el visor; `problem`/`fix`, para el usuario."""

    def __init__(self, kind: str, problem: str, fix: str) -> None:
        super().__init__(problem)
        self.kind = kind
        self.problem = problem
        self.fix = fix


class Provider(Protocol):
    id: str
    name: str
    model: str
    simulated: bool

    def available(self) -> str | None:
        """None si está listo; si no, el motivo (sin revelar secretos)."""

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_output_tokens: int,
        timeout_s: float,
    ) -> Completion: ...


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


@dataclass
class AnthropicProvider:
    """Claude por la Messages API (SDK oficial `anthropic`)."""

    id: str = "anthropic"
    name: str = "Claude (Anthropic)"
    model: str = field(
        default_factory=lambda: _env("EXTRUDE_ANTHROPIC_MODEL") or ANTHROPIC_DEFAULT_MODEL
    )
    effort: str = field(
        default_factory=lambda: _env("EXTRUDE_ANTHROPIC_EFFORT") or ANTHROPIC_DEFAULT_EFFORT
    )
    base_url: str | None = field(default_factory=lambda: _env("EXTRUDE_ANTHROPIC_BASE_URL") or None)
    simulated: bool = False

    def available(self) -> str | None:
        if not _env("ANTHROPIC_API_KEY"):
            return "Define ANTHROPIC_API_KEY en el entorno del servidor y reinícialo."
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return "Instala el SDK: cd pipeline && uv sync --extra ai"
        return None

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_output_tokens: int,
        timeout_s: float,
    ) -> Completion:
        import anthropic

        client = anthropic.Anthropic(
            api_key=_env("ANTHROPIC_API_KEY"),
            max_retries=0,
            timeout=timeout_s,
            base_url=self.base_url,
        )
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if self.effort:
            output_config["effort"] = self.effort
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": output_config,
        }
        try:
            response = client.messages.create(**request)
        except anthropic.APITimeoutError as exc:
            raise _timeout(self.name) from exc
        except anthropic.APIConnectionError as exc:
            raise _network(self.name) from exc
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise _auth(self.name, "ANTHROPIC_API_KEY") from exc
        except anthropic.RateLimitError as exc:
            raise _quota(self.name) from exc
        except anthropic.NotFoundError as exc:
            raise _model(self.name, self.model, "EXTRUDE_ANTHROPIC_MODEL") from exc
        except anthropic.APIStatusError as exc:
            raise _status(self.name, exc.status_code) from exc
        usage = Usage(
            getattr(response.usage, "input_tokens", None),
            getattr(response.usage, "output_tokens", None),
        )
        if response.stop_reason == "max_tokens":
            raise _truncated(self.name, usage)
        if response.stop_reason == "refusal":
            raise ProviderError(
                "refused",
                f"{self.name} rechazó la solicitud.",
                "Reformula la descripción o asigna los materiales con el formulario.",
            )
        texts = [b.text for b in response.content if b.type == "text"]
        if len(texts) != 1:
            raise ProviderError(
                "invalid",
                f"{self.name} no devolvió una única respuesta JSON.",
                "No se aplicó nada. Reformula o asigna manualmente.",
            )
        return Completion(texts[0], usage, response.model or self.model)


@dataclass
class OpenAIProvider:
    """OpenAI por la Responses API (SDK oficial `openai`), sin almacenamiento (`store=False`)."""

    id: str = "openai"
    name: str = "OpenAI"
    model: str = field(default_factory=lambda: _env("EXTRUDE_OPENAI_MODEL"))
    reasoning_effort: str = field(default_factory=lambda: _env("EXTRUDE_OPENAI_REASONING_EFFORT"))
    base_url: str | None = field(default_factory=lambda: _env("EXTRUDE_OPENAI_BASE_URL") or None)
    simulated: bool = False

    def available(self) -> str | None:
        if not _env("OPENAI_API_KEY"):
            return "Define OPENAI_API_KEY en el entorno del servidor y reinícialo."
        if not self.model:
            return "Define EXTRUDE_OPENAI_MODEL (p. ej. el modelo contratado) y reinicia."
        try:
            import openai  # noqa: F401
        except ImportError:
            return "Instala el SDK: cd pipeline && uv sync --extra ai"
        return None

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_output_tokens: int,
        timeout_s: float,
    ) -> Completion:
        import openai

        client = openai.OpenAI(
            api_key=_env("OPENAI_API_KEY"),
            max_retries=0,
            timeout=timeout_s,
            base_url=self.base_url,
        )
        extra: dict[str, Any] = {}
        if self.reasoning_effort:
            extra["reasoning"] = {"effort": self.reasoning_effort}
        try:
            response = client.responses.create(
                model=self.model,
                instructions=system,
                input=user,
                max_output_tokens=max_output_tokens,
                store=False,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "material_proposal",
                        "schema": schema,
                        "strict": True,
                    }
                },
                **extra,
            )
        except openai.APITimeoutError as exc:
            raise _timeout(self.name) from exc
        except openai.APIConnectionError as exc:
            raise _network(self.name) from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise _auth(self.name, "OPENAI_API_KEY") from exc
        except openai.RateLimitError as exc:
            raise _quota(self.name) from exc
        except openai.NotFoundError as exc:
            raise _model(self.name, self.model, "EXTRUDE_OPENAI_MODEL") from exc
        except openai.APIStatusError as exc:
            raise _status(self.name, exc.status_code) from exc
        raw_usage = response.usage
        usage = Usage(
            getattr(raw_usage, "input_tokens", None) if raw_usage else None,
            getattr(raw_usage, "output_tokens", None) if raw_usage else None,
        )
        if response.status == "incomplete":
            raise _truncated(self.name, usage)
        if response.status != "completed":
            raise ProviderError(
                "provider",
                f"{self.name} no completó la respuesta ({response.status}).",
                "No se aplicó nada. Puedes reintentar o asignar manualmente.",
            )
        texts: list[str] = []
        for item in response.output:
            if item.type != "message":
                continue
            for content in item.content:
                if content.type == "refusal":
                    raise ProviderError(
                        "refused",
                        f"{self.name} rechazó la solicitud.",
                        "Reformula la descripción o asigna los materiales con el formulario.",
                    )
                if content.type == "output_text":
                    texts.append(content.text)
        if len(texts) != 1:
            raise ProviderError(
                "invalid",
                f"{self.name} no devolvió una única respuesta JSON.",
                "No se aplicó nada. Reformula o asigna manualmente.",
            )
        return Completion(texts[0], usage, response.model or self.model)


def _timeout(name: str) -> ProviderError:
    return ProviderError(
        "timeout",
        f"{name} no respondió en el tiempo límite.",
        "No se reintenta solo. Acota el alcance o vuelve a pulsar si quieres reintentar.",
    )


def _network(name: str) -> ProviderError:
    return ProviderError(
        "network",
        f"No se pudo conectar con {name}.",
        "Comprueba la conexión del servidor. El formulario manual sigue disponible.",
    )


def _auth(name: str, variable: str) -> ProviderError:
    return ProviderError(
        "auth",
        f"{name} rechazó la clave o sus permisos.",
        f"Revisa {variable} en el entorno del servidor (no en el visor) y reinícialo.",
    )


def _quota(name: str) -> ProviderError:
    return ProviderError(
        "quota",
        f"{name} indica límite de uso o cuota agotada.",
        "Espera o revisa el saldo/límites de la cuenta. No se reintenta automáticamente.",
    )


def _model(name: str, model: str, variable: str) -> ProviderError:
    return ProviderError(
        "model",
        f"{name} no reconoce el modelo «{model}».",
        f"Revisa {variable} en el entorno del servidor.",
    )


def _status(name: str, status: int) -> ProviderError:
    return ProviderError(
        "provider",
        f"{name} devolvió un error HTTP {status}.",
        "No se aplicó nada. Puedes reintentar más tarde o asignar manualmente.",
    )


def _truncated(name: str, usage: Usage) -> ProviderError:
    error = ProviderError(
        "truncated",
        f"La respuesta de {name} no cabe en el límite de salida y se ha descartado.",
        "Acota el alcance (marca menos edificios o elige una capa) o simplifica la descripción.",
    )
    error.usage = usage  # type: ignore[attr-defined]
    return error


# --- Simulado ------------------------------------------------------------------------------------

_SURFACE_WORDS: list[tuple[str, str]] = [
    (r"fachadas? (?:de|del|a) patios?|patios?", "facade_courtyard"),
    (r"cubiertas?|tejados?|azoteas?", "roof"),
    (r"z[oó]calos?", "foundation"),
    (r"fachadas?|muros?", "facade_exterior"),
    (r"suelos?|pavimentos?|calles?|viales?", "ground"),
]
_FAMILY_SURFACE = {
    "cubierta": "roof",
    "pavimento": "ground",
    "vegetación": "ground",
    "agua": "ground",
}
_UNSUPPORTED = [
    ("hueco", "facade.openings_cut"),
    ("perfora", "facade.openings_cut"),
    ("puerta", "facade.openings_cut"),
    ("pendiente", "roof.slope_change"),
    ("roble", "site.species"),
    ("pino", "site.species"),
    ("olivo", "site.species"),
    ("platano", "site.species"),
    ("camino", "site.paths_terrain"),
    ("terreno", "site.paths_terrain"),
    ("imagen", "reference.images"),
    ("foto", "reference.images"),
]
_NUMBERS = {
    "un": 1,
    "una": 1,
    "uno": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
}
_DIRECTION = {"norte": (0.0, 1.0), "sur": (0.0, -1.0), "este": (1.0, 0.0), "oeste": (-1.0, 0.0)}
_SIDES = {"norte": "north", "sur": "south", "este": "east", "oeste": "west"}
_NUMBER = r"(\d+(?:[.,]\d+)?|un|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)"
_TIMES = {"sunset": "Atardecer", "sunrise": "Amanecer", "noon": "Mediodía"}


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _num(token: str) -> float:
    return float(_NUMBERS.get(token, token.replace(",", ".")))


@dataclass
class SimulatedProvider:
    """Intérprete local por reglas (sin red). Misma interfaz y mismo contrato que los reales."""

    id: str = "simulated"
    name: str = "Simulado (local, sin IA)"
    model: str = "reglas-locales-3"
    simulated: bool = True
    delay_s: float = 0.0

    def available(self) -> str | None:
        return None

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_output_tokens: int,
        timeout_s: float,
    ) -> Completion:
        if self.delay_s:
            time.sleep(self.delay_s)
        result = interpret_locally(json.loads(user))
        return Completion(json.dumps(result, ensure_ascii=False), Usage(None, None), self.model)


def _material_terms(materials: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    terms = [(_fold(m["name"]), m) for m in materials]
    terms += [(m["id"].replace("_", " "), m) for m in materials]
    by_id = {m["id"]: m for m in materials}
    generic = {
        "ladrillo": "brick_light",
        "grava": "roof_gravel",
        "hormigon": "concrete_exposed",
        "piedra": "stone_grey",
        "asfalto": "asphalt",
        "madera": "wood_exterior",
        "vidrio": "glass_blue",
        "revoco": "render_white",
        "enfoscado": "render_white",
        "blanco": "render_white",
    }
    terms += [(w, by_id[i]) for w, i in generic.items() if i in by_id]
    terms.sort(key=lambda t: -len(t[0]))  # «ladrillo claro» antes que «ladrillo»
    return terms


def _targets(**overrides: Any) -> dict[str, Any]:
    return {"refs": [], "layers": [], "blocks": [], "kinds": [], "pending": None, **overrides}


class _Plan:
    def __init__(self, rows: list[dict[str, Any]], excluded: list[str]) -> None:
        self.rows = rows
        self.excluded = excluded
        self.data: dict[str, Any] = {
            "summary": "",
            "explicitRequirements": [],
            "assumptions": [],
            "operations": [],
            "pending": [],
            "conflicts": [],
            "unsupported": [],
        }

    def add(
        self,
        tool: str,
        args: dict[str, Any],
        target: dict[str, Any],
        basis: str = "explicit",
        why: str = "Pedido por el usuario.",
    ) -> None:
        if tool == "material.assign":
            args = {
                "color": None,
                "roughness": None,
                "pieceSizeM": None,
                "rotationDeg": None,
                **args,
            }
        self.data["operations"].append(
            {
                "id": f"op{len(self.data['operations']) + 1}",
                "tool": tool,
                "args": args,
                "targets": {
                    **target,
                    "except": [e for e in self.excluded if e not in target["refs"]],
                },
                "dependsOn": [],
                "basis": basis,
                "reason": why,
            }
        )

    def pending(self, reference: str, question: str, **rule: Any) -> dict[str, Any]:
        pid = f"q{len(self.data['pending']) + 1}"
        self.data["pending"].append(
            {
                "id": pid,
                "reference": reference,
                "question": question,
                "candidates": [],
                "rule": {
                    "kind": "none",
                    "side": None,
                    "by": None,
                    "order": None,
                    "count": None,
                    **rule,
                },
            }
        )
        return _targets(pending=pid)


def _target_for(
    clause: str, plan: _Plan, labels: dict[str, str], current: dict[str, Any] | None
) -> dict[str, Any] | None:
    found = re.findall(r"[a-z]+-\d+|[a-z_]+ #\d+(?:\.\d+)?", clause.split("salvo")[0])
    refs = [labels[t] for t in found if t in labels]
    side = next(
        (
            d
            for d in _SIDES
            if re.search(rf"\b(los|las|del|de la)\s+(del\s+)?{d}\b", clause)
            and not re.search(rf"\bal\s+{d}\b", clause)
        ),
        None,
    )
    if refs:
        return _targets(refs=refs)
    if "central" in clause:
        return plan.pending(
            "la torre central",
            "¿Es este el edificio central? Confírmalo.",
            kind="rank",
            by="centrality",
            order="desc",
            count=1,
        )
    if re.search(r"mas alt[oa]", clause):
        return plan.pending(
            "el más alto",
            "¿Es este el edificio más alto al que te refieres?",
            kind="rank",
            by="height",
            order="desc",
            count=1,
        )
    if side:
        return plan.pending(
            f"los del {side}",
            f"¿Qué edificios son los del {side}? Confirma o elige otros.",
            kind="side",
            side=_SIDES[side],
        )
    if re.search(r"seleccion|marcad|\bestos\b|\bestas\b", clause):
        return _targets(refs=[r["ref"] for r in plan.rows if r["selected"]])
    if re.search(r"\btod[oa]s\b|edificios|conjunto", clause) or current is None:
        return _targets(kinds=["building"])
    return current


def _geometry(clause: str, plan: _Plan, target: dict[str, Any]) -> str | None:
    """Altura, mover, girar o duplicar; devuelve el resumen o None si no es geométrica."""
    floors = re.search(
        rf"(sube|subir|eleva|anade|baja|bajar|quita).*?\s{_NUMBER}\s+plantas?", clause
    )
    metres = re.search(rf"(sube|subir|eleva|baja|bajar).*?\s{_NUMBER}\s*m(etros)?\b", clause)
    height = re.search(rf"altura de\s+{_NUMBER}\s*m", clause)
    way = r"\s*m(etros)?\s+(hacia el|al)\s+(norte|sur|este|oeste)"
    move = re.search(rf"(mueve|desplaza|mover).*?\s{_NUMBER}{way}", clause)
    dup = re.search(rf"(duplica|copia).*?\s{_NUMBER}{way}", clause)
    rotate = re.search(rf"(gira|rota|girar).*?\s{_NUMBER}\s*(grados|º)", clause)
    if floors:
        sign = -1 if floors.group(1) in ("baja", "bajar", "quita") else 1
        plan.add(
            "building.set_height",
            {"mode": "delta_floors", "value": sign * _num(floors.group(2)), "floorHeightM": None},
            target,
        )
        return f"{'subir' if sign > 0 else 'bajar'} {floors.group(2)} plantas"
    if metres:
        delta = (-1 if metres.group(1).startswith("baj") else 1) * _num(metres.group(2))
        plan.add(
            "building.set_height", {"mode": "delta_m", "value": delta, "floorHeightM": None}, target
        )
        return f"altura {delta:+g} m"
    if height:
        plan.add(
            "building.set_height",
            {"mode": "absolute_m", "value": _num(height.group(1)), "floorHeightM": None},
            target,
        )
        return f"altura de {height.group(1)} m"
    for found, tool in ((move, "object.move"), (dup, "object.duplicate")):
        if found:
            ux, uy = _DIRECTION[found.group(5)]
            d = _num(found.group(2))
            args: dict[str, Any] = {"dxM": ux * d, "dyM": uy * d}
            if tool == "object.duplicate":
                args["copies"] = 1
            plan.add(tool, args, target)
            verb = "mover" if tool == "object.move" else "duplicar a"
            return f"{verb} {d:g} m al {found.group(5)}"
    if rotate:
        plan.add("object.rotate", {"angleDeg": _num(rotate.group(2))}, target)
        return f"girar {rotate.group(2)}°"
    return None


_ASSET_WORDS = [
    (
        r"cipres|arbol(?:es)? (?:de porte )?(?:vertical|columnar)",
        "tree_columnar",
        "árboles columnares",
    ),
    (r"arbol", "tree_round", "árboles"),
    (r"arbust|seto", "shrub", "arbustos"),
    (r"banco", "bench", "bancos"),
    (r"farola", "street_lamp", "farolas"),
    (r"papelera", "litter_bin", "papeleras"),
]
_ORIENT_RE = r"(?:fachadas?\s+|orientad[oa]s?\s+al\s+|al\s+|a\s+)(norte|sur|este|oeste)"


def _orientations(clause: str) -> list[str]:
    found = [_SIDES[d] for d in re.findall(_ORIENT_RE, clause)]
    return list(dict.fromkeys(found)) or ["north", "south", "east", "west"]


def _floors(clause: str) -> list[int] | None:
    match = re.search(r"plantas?\s+((?:\d+(?:\s*(?:,|y|a)\s*)?)+)", clause)
    if not match:
        return None
    nums = [int(n) for n in re.findall(r"\d+", match.group(1))]
    if re.search(r"\d+\s*a\s*\d+", match.group(1)) and len(nums) == 2:
        return list(range(nums[0], nums[1] + 1))
    return nums or None


def _extended(clause: str, plan: _Plan, target: dict[str, Any]) -> str | None:
    """Entorno, fachadas, retranqueos, copias independientes y cubiertas (ampliación)."""
    m = rf"\s*{_NUMBER}\s*m(?:etros)?\b"
    if re.search(r"independiza|haz(?:la)? unica|separa(?:r)? (?:esta|la|esa) copia", clause):
        plan.add("block.make_unique", {"confirm": True}, target)
        return "independizar la copia"
    walls = re.search(rf"(?:sube|subir|eleva|levanta)\w*\s+(?:los\s+)?muros.*?{m}", clause)
    if walls:
        plan.add("building.raise_walls", {"deltaM": _num(walls.group(1))}, target)
        return f"subir muros {walls.group(1)} m conservando la cubierta"
    ridge = re.search(rf"cumbrera.*?{m}", clause)
    if ridge:
        plan.add("roof.set_ridge", {"ridgeHeightM": _num(ridge.group(1))}, target)
        return f"cumbrera a {ridge.group(1)} m sobre el alero"
    if re.search(r"retranque|atico", clause):
        depth = re.search(rf"(?:retranque\w*|atico)\D*?{m}", clause)
        start = re.search(rf"(?:a partir de|desde)(?:\s+los)?{m}", clause)
        upper = re.search(rf"(?:atico|planta superior) de{m}", clause)
        sides = [d for d in re.findall(r"al\s+(norte|sur|este|oeste)", clause)]
        if not start:
            heights = [r["h"] for r in plan.rows if r["ref"] in target["refs"] and r["h"]]
            if not heights:
                return None
            start_m = float(min(heights))  # ático encima: el cuerpo actual se conserva
        else:
            start_m = _num(start.group(1))
        plan.add(
            "building.setback",
            {
                "startHeightM": start_m,
                "setbackM": _num(depth.group(1)) if depth else 2.0,
                "upperHeightM": _num(upper.group(1)) if upper else 3.0,
                "orientations": [_SIDES[d] for d in sides] or ["all"],
            },
            target,
            "explicit" if depth else "assumption",
            "Pedido por el usuario." if depth else "Supuesto: retranqueo de 2 m.",
        )
        return "retranqueo" + (f" de {depth.group(1)} m" if depth else " (2 m, supuesto)")
    if "ventana" in clause:
        for side in _orientations(clause):
            plan.add(
                "facade.add_windows",
                {
                    "orientation": side,
                    "widthM": 1.2,
                    "heightM": 1.5,
                    "sillM": 0.9,
                    "depthM": 0.1,
                    "spacingM": 1.2,
                    "columns": None,
                    "floors": _floors(clause),
                    "floorHeightM": None,
                    "glassMaterialId": None,
                    "frameMaterialId": None,
                },
                target,
            )
        return "añadir ventanas"
    if "balcon" in clause:
        for side in _orientations(clause):
            plan.add(
                "facade.add_balconies",
                {
                    "orientation": side,
                    "widthM": 3.0,
                    "projectionM": 1.2,
                    "thicknessM": 0.2,
                    "railingHeightM": 1.0,
                    "floors": _floors(clause),
                    "floorHeightM": None,
                    "materialId": None,
                },
                target,
            )
        return "añadir balcones"
    asset = next(((a, n) for w, a, n in _ASSET_WORDS if re.search(w, clause)), None)
    if asset:
        count = re.search(
            rf"{_NUMBER}\s+(?:\w+\s+)?(?:{'|'.join(w for w, _, _ in _ASSET_WORDS)})", clause
        )
        spacing = re.search(rf"cada{m}", clause)
        base = re.search(r"cota(?: de)?\s*(-?\d+(?:[.,]\d+)?)\s*m", clause)
        layout = (
            "around"
            if "alrededor" in clause
            else "row"
            if target["refs"] or re.search(r"a lo largo|junto|fila|aline", clause)
            else "point"
        )
        side = "both" if "ambos lados" in clause else "right" if "derecha" in clause else "left"
        plan.add(
            "site.place",
            {
                "assetId": asset[0],
                "layout": layout,
                "count": int(_num(count.group(1))) if count else (4 if layout != "point" else 1),
                "spacingM": _num(spacing.group(1)) if spacing else None,
                "sides": "center" if layout == "around" else side,
                "sideOffsetM": 2.0,
                "facing": "target" if layout == "around" else "along",
                "rotationDeg": None,
                "scale": 1.0,
                "xM": 0.0 if layout == "point" else None,
                "yM": 0.0 if layout == "point" else None,
                # Sin terreno la cota se pide al usuario (nunca se inventa)
                "support": "elevation",
                "baseZM": float(base.group(1).replace(",", ".")) if base else None,
            },
            target,
        )
        return f"colocar {asset[1]}"
    delete = re.search(r"^(?:elimina|borra|quita)\b", clause)
    if delete and "planta" not in clause and target["refs"]:
        plan.add("object.delete", {"confirm": True}, target)
        return "eliminar"
    return None


def _link_unique(operations: list[dict[str, Any]]) -> None:
    """Lo que sigue a «independizar» sobre los mismos objetos depende de esa operación."""
    for i, first in enumerate(operations):
        if first["tool"] != "block.make_unique":
            continue
        for later in operations[i + 1 :]:
            if set(later["targets"]["refs"]) & set(first["targets"]["refs"]):
                later["dependsOn"].append(first["id"])


def interpret_locally(context: dict[str, Any]) -> dict[str, Any]:
    """Reglas sencillas y deterministas sobre el mismo contexto que recibiría el modelo."""
    request = _fold(context["request"])
    columns = context["objects"]["columns"]
    rows = [dict(zip(columns, r, strict=True)) for r in context["objects"]["rows"]]
    labels = {_fold(r["label"]): r["ref"] for r in rows}
    terms = _material_terms(context["materials"])
    excluded: list[str] = []
    for match in re.finditer(r"(?:salvo|excepto|menos)\s+([^.;]+)", request):
        excluded += [labels[t] for t in re.findall(r"[a-z]+-\d+", match.group(1)) if t in labels]
    plan = _Plan(rows, excluded)
    data = plan.data
    split = r"\.(?!\d)|;|,(?!\s*\d)| y (?![a-z]+-\d|\d)"
    clauses = [c.strip(" ,") for c in re.split(split, request) if c.strip(" ,")]
    target: dict[str, Any] | None = None
    summary: list[str] = []
    for raw_clause in clauses:
        clause = raw_clause
        if re.match(r"^(manten|conserva|deja)", clause):
            data["explicitRequirements"].append("Mantener el resto del proyecto sin cambios")
            clause = re.sub(
                r"^(manten|conserva|deja)\w*\s+(todo\s+)?(como esta\s*)?(pero\s*)?", "", clause
            ).strip()
            if not clause:
                continue
        clause = re.sub(r"^pero\s+", "", clause)
        if not any(word in clause for word, _ in _UNSUPPORTED):
            asked = len(plan.data["pending"])
            here = _target_for(clause, plan, labels, target)
            done = _extended(clause, plan, here or _targets())
            if done is None:
                del plan.data["pending"][asked:]  # la duda se volverá a plantear abajo
            else:
                target = here or target
                summary.append(done)
                data["explicitRequirements"].append(clause)
                continue
        gap = next((cap for word, cap in _UNSUPPORTED if word in clause), None)
        if gap:
            data["unsupported"].append(
                {
                    "request": clause,
                    "capability": gap,
                    "reason": "No hay herramienta para esa geometría; una textura no la crea.",
                }
            )
            continue
        if "vista" in clause or re.search(r"atardecer|amanecer|ocaso|mediodia", clause):
            moment = (
                "sunset"
                if re.search(r"atardecer|ocaso", clause)
                else "sunrise"
                if "amanecer" in clause
                else "noon"
                if "mediodia" in clause
                else None
            )
            if "vista" in clause:
                name = _TIMES.get(moment or "", "Vista")
                plan.add(
                    "view.create",
                    {"name": name, "preset": "iso", "time": moment, "date": None},
                    _targets(),
                )
                summary.append(f"crear la vista «{name}»")
            elif moment:
                plan.add("scene.set_sun", {"time": moment, "date": None}, _targets())
                summary.append("cambiar la hora del sol")
            data["explicitRequirements"].append(clause)
            continue
        target = _target_for(clause, plan, labels, target) or _targets(kinds=["building"])
        if "mediterrane" in clause:
            plan.add(
                "material.assign",
                {"surface": "facade_exterior", "materialId": "render_white"},
                target,
                "assumption",
                "Supuesto: «mediterráneo» como fachadas encaladas.",
            )
            data["assumptions"].append(
                "«Mediterráneo» se traduce en fachadas de revestimiento blanco (revísalo)."
            )
            summary.append("fachadas blancas (supuesto)")
            continue
        done = _geometry(clause, plan, target)
        if done is None:
            material = next((m for term, m in terms if term in clause), None)
            if material is None:
                continue
            surface = next(
                (s for pattern, s in _SURFACE_WORDS if re.search(rf"\b(?:{pattern})\b", clause)),
                _FAMILY_SURFACE.get(material["family"], "facade_exterior"),
            )
            plan.add("material.assign", {"surface": surface, "materialId": material["id"]}, target)
            done = f"{material['name']} en {context['surfaces'].get(surface, surface)}"
        summary.append(done)
        data["explicitRequirements"].append(clause)
    _link_unique(data["operations"])
    if excluded:
        names = ", ".join(r["label"] for r in rows if r["ref"] in excluded)
        data["explicitRequirements"].append(f"Excluir {names}")
        summary.append(f"excepto {names}")
    data["summary"] = (
        "Aplicar: " + "; ".join(summary) + "."
        if summary
        else "No he encontrado cambios ejecutables."
    )
    return data
