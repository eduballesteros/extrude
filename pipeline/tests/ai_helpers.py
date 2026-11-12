"""Utilidades de pruebas de la edición con IA: proveedor falso que cuenta llamadas y respuestas
del contrato v2 construidas a partir del contexto que recibe (referencias reales, no inventadas)."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from extrude.ai_planner import AiLimits, AiService, ProposalRequest
from extrude.ai_providers import Completion, ProviderError, Usage

Responder = Callable[[dict[str, Any]], str]


@dataclass
class FakeProvider:
    responder: Responder
    id: str = "fake"
    name: str = "Fake"
    model: str = "fake-1"
    simulated: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)
    error: ProviderError | None = None
    gate: threading.Event | None = None

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
        self.calls.append({"system": system, "user": user, "schema": schema})
        if self.gate is not None:
            self.gate.wait(5)
        if self.error is not None:
            raise self.error
        return Completion(self.responder(json.loads(user)), Usage(1200, 300), self.model)


def refs(context: dict[str, Any], *labels: str) -> list[str]:
    by_label = {r[1]: r[0] for r in context["objects"]["rows"]}
    return [by_label[label] for label in labels]


def targets(refs_: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "refs": refs_ or [],
        "layers": [],
        "blocks": [],
        "kinds": [],
        "except": [],
        "pending": None,
        **extra,
    }


def op(
    op_id: str,
    tool: str,
    args: dict[str, Any],
    target: dict[str, Any],
    *,
    depends: list[str] | None = None,
    basis: str = "explicit",
) -> dict[str, Any]:
    if tool == "material.assign":
        args = {"color": None, "roughness": None, "pieceSizeM": None, "rotationDeg": None, **args}
    return {
        "id": op_id,
        "tool": tool,
        "args": args,
        "targets": target,
        "dependsOn": depends or [],
        "basis": basis,
        "reason": "Pedido por el usuario.",
    }


def plan(*operations: dict[str, Any], **extra: Any) -> str:
    data: dict[str, Any] = {
        "summary": "Cambios pedidos.",
        "explicitRequirements": [],
        "assumptions": [],
        "operations": list(operations),
        "pending": [],
        "conflicts": [],
        "unsupported": [],
    }
    data.update(extra)
    return json.dumps(data)


def brick(
    *labels: str, surface: str = "facade_exterior", material: str = "brick_light", **params: Any
) -> Responder:
    return lambda ctx: plan(
        op(
            "op1",
            "material.assign",
            {"surface": surface, "materialId": material, **params},
            targets(refs(ctx, *labels)),
        )
    )


def service(provider: Any, **limits: Any) -> AiService:
    return AiService({provider.id: provider}, AiLimits(**limits), prices={})


def ask(
    svc: AiService, job: Path, description: str = "Fachadas de ladrillo claro", **kw: Any
) -> dict[str, Any]:
    provider = next(iter(svc.providers))
    request = ProposalRequest.model_validate(
        {"provider": provider, "description": description, "consent": True, **kw}
    )
    return svc.propose("job1", job, request)


def ids_of(job: Path, *labels: str) -> list[str]:
    from extrude.ai_planner import load_project

    by_label = {o.label: o.id for o in load_project(job / "out").objects}
    return [by_label[label] for label in labels]


def copy_job(model_dir: Path, job: Path) -> Path:
    (job / "out").mkdir(parents=True)
    for name in ("model.glb", "objects.json"):
        (job / "out" / name).write_bytes((model_dir / name).read_bytes())
    return job


SUN = {
    "mode": "datetime",
    "isoDate": "2024-06-21T21:30:00+02:00",
    "lat": 40.4,
    "lon": -3.7,
    "azimuthDeg": 300.0,
    "elevationDeg": 1.0,
    "northRotationDeg": 0.0,
    "intensity": 3.0,
    "colorTempK": 3200.0,
}
CAMERA = {
    "type": "perspective",
    "position": [40.0, 30.0, 40.0],
    "target": [0.0, 0.0, 0.0],
    "up": [0.0, 1.0, 0.0],
    "fovDeg": 45.0,
    "orthoSize": 50.0,
    "near": 0.1,
    "far": 5000.0,
}


def prepared_viewer(views: list[dict[str, Any]]) -> dict[str, Any]:
    """Efectos que el visor prepararía antes de confirmar (cámara y sol finales, tipados)."""
    timed = any(o["args"].get("time") for o in views)
    return {
        "views": [
            {
                "id": f"view-{o['id']}-0000",
                "name": o["args"]["name"],
                "state": {"camera": CAMERA, "sun": SUN},
            }
            for o in views
        ],
        "sunBefore": SUN if timed else None,
        "sunAfter": SUN if timed else None,
    }
