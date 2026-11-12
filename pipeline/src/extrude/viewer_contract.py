"""Contrato tipado del estado del visor que se guarda con cada revisión (vistas, cámara y sol).

El visor prepara este estado ANTES de confirmar (cámaras finales y horas solares calculadas en su
ubicación). El servidor no puede recalcular la hora de un «atardecer», pero sí comprueba:

- tipos, rangos y valores finitos de cámara y sol;
- que haya exactamente una vista preparada por cada `view.create`, con su nombre;
- que un cambio de sol (en `scene.set_sun` o en una vista) traiga el sol resultante;
- que una hora «HH:MM» pedida sea la hora del sol preparado.

`viewer={}` o un sol ausente no valen como preparación de un cambio de iluminación.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

MAX_VIEWER_BYTES = 512 * 1024
Finite = Annotated[float, Field(allow_inf_nan=False)]
Vec3 = tuple[Finite, Finite, Finite]
ISO = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Camera(_Model):
    type: Literal["perspective", "orthographic"]
    position: Vec3
    target: Vec3
    up: Vec3
    fovDeg: float = Field(gt=0, lt=180)  # noqa: N815
    orthoSize: float = Field(gt=0)  # noqa: N815
    near: float = Field(gt=0)
    far: float = Field(gt=0)


class Sun(_Model):
    mode: Literal["datetime", "manual"]
    isoDate: Annotated[str, StringConstraints(pattern=ISO.pattern)]  # noqa: N815
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    azimuthDeg: Finite  # noqa: N815
    elevationDeg: float = Field(ge=-90, le=90)  # noqa: N815
    northRotationDeg: Finite  # noqa: N815
    intensity: float = Field(ge=0, le=100)
    colorTempK: float = Field(ge=1000, le=20000)  # noqa: N815


class State(BaseModel):
    """Estado de una vista: cámara y sol estrictos; el resto (capas, luces…) es presentación."""

    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    camera: Camera
    sun: Sun


class PreparedView(_Model):
    id: Annotated[str, StringConstraints(pattern=r"^[0-9A-Za-z-]{8,64}$")]
    name: Annotated[str, StringConstraints(min_length=1, max_length=60)]
    state: State


class ViewerEffects(_Model):
    views: list[PreparedView] = Field(max_length=50)
    sunBefore: Sun | None  # noqa: N815
    sunAfter: Sun | None  # noqa: N815


class ViewerContractError(ValueError):
    pass


def _hhmm(iso: str) -> str:
    match = ISO.match(iso)
    return f"{match.group(2)}:{match.group(3)}" if match else ""


def validate_effects(data: dict[str, Any] | None, ops: list[dict[str, Any]]) -> dict[str, Any]:
    """Estado del visor validado frente a las operaciones del visor que se confirman."""
    if data is None:
        raise ViewerContractError("Las vistas o el sol no se prepararon en el visor.")
    if len(json.dumps(data)) > MAX_VIEWER_BYTES:
        raise ViewerContractError("El estado del visor es demasiado grande (crea menos vistas).")
    try:
        effects = ViewerEffects.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise ViewerContractError(f"Estado del visor no válido en {loc}: {first['msg']}") from None
    view_ops = [op for op in ops if op["tool"] == "view.create"]
    if [v.name for v in effects.views] != [str(op["args"]["name"]) for op in view_ops]:
        raise ViewerContractError("Las vistas preparadas no coinciden con las del plan.")
    times = [op["args"].get("time") for op in ops if op["args"].get("time")]
    if times and effects.sunAfter is None:
        raise ViewerContractError("El plan cambia el sol y no se preparó el sol resultante.")
    last = times[-1] if times else None
    if (
        effects.sunAfter is not None
        and isinstance(last, str)
        and re.fullmatch(r"\d{2}:\d{2}", last)
        and _hhmm(effects.sunAfter.isoDate) != last
    ):
        raise ViewerContractError(
            f"El sol preparado ({_hhmm(effects.sunAfter.isoDate)}) no es la hora pedida ({last})."
        )
    return effects.model_dump(mode="json")
