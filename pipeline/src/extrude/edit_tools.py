"""Registro de herramientas de edición: lo único que la IA puede planificar.

La IA interpreta y planifica; Extrude ejecuta operaciones tipadas. Cada herramienta declara
nombre y versión, capacidad, esquema de argumentos, objetivos compatibles, precondiciones,
efectos, cómo se previsualiza/confirma/deshace y quién la ejecuta (`server`: GLB y plan de
materiales; `viewer`: vistas y sol del visor). Lo que aún no existe está en `PLANNED`: la IA lo
devuelve como «no ejecutable» con la capacidad que falta, nunca como herramienta.

Para añadir una herramienta: un modelo de argumentos (pydantic estricto), un `ToolSpec` con su
`check` (compatibilidad por objeto) y su ejecución en project_edit.py o en el visor.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from extrude.material_library import DEFAULT_LIBRARY, MaterialLibrary

SURFACES = [
    "generic",
    "facade_exterior",
    "facade_courtyard",
    "roof",
    "foundation",
    "ground",
    "underside",
]


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


Piece = Annotated[float, Field(gt=0, le=20)]


class MaterialArgs(_Args):
    surface: Literal[
        "generic",
        "facade_exterior",
        "facade_courtyard",
        "roof",
        "foundation",
        "ground",
        "underside",
    ]
    materialId: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,47}$")]  # noqa: N815
    color: Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")] | None
    roughness: float | None = Field(ge=0, le=1)
    # Tamaño real de UNA pieza (ladrillo, losa, tabla) en metros [ancho, alto]; el servidor lo
    # convierte en el mosaico de textura (que contiene varias piezas).
    pieceSizeM: list[Piece] | None = Field(min_length=2, max_length=2)  # noqa: N815
    rotationDeg: float | None = Field(ge=-360, le=360)  # noqa: N815


class HeightArgs(_Args):
    mode: Literal["absolute_m", "delta_m", "absolute_floors", "delta_floors"]
    value: float = Field(ge=-500, le=1000)
    # Solo si el usuario la da: si falta y el edificio no tiene altura de planta configurada,
    # la operación queda pendiente de ese dato (no se inventa).
    floorHeightM: float | None = Field(gt=1.5, le=10)  # noqa: N815


class MoveArgs(_Args):
    dxM: float = Field(ge=-5000, le=5000)  # noqa: N815 (este +)
    dyM: float = Field(ge=-5000, le=5000)  # noqa: N815 (norte del plano +)


class RotateArgs(_Args):
    angleDeg: float = Field(ge=-360, le=360)  # noqa: N815 (antihorario en planta)


class DuplicateArgs(_Args):
    dxM: float = Field(ge=-5000, le=5000)  # noqa: N815
    dyM: float = Field(ge=-5000, le=5000)  # noqa: N815
    copies: int = Field(ge=1, le=20)


Orientation = Literal["north", "south", "east", "west"]
Floors = list[Annotated[int, Field(ge=0, le=200)]]


class SitePlaceArgs(_Args):
    assetId: Literal[  # noqa: N815
        "tree_round", "tree_columnar", "shrub", "bench", "street_lamp", "litter_bin"
    ]
    layout: Literal["row", "around", "point"]
    count: int = Field(ge=1, le=200)
    spacingM: float | None = Field(gt=0, le=200)  # noqa: N815
    sides: Literal["center", "left", "right", "both"]
    sideOffsetM: float = Field(ge=0, le=50)  # noqa: N815
    facing: Literal["along", "target", "fixed"]
    rotationDeg: float | None = Field(ge=-360, le=360)  # noqa: N815
    scale: float = Field(ge=0.5, le=2.0)
    xM: float | None = Field(ge=-5000, le=5000)  # noqa: N815
    yM: float | None = Field(ge=-5000, le=5000)  # noqa: N815
    support: Literal["terrain", "target_surface", "elevation"]
    baseZM: float | None = Field(ge=-1000, le=9000)  # noqa: N815


class DeleteArgs(_Args):
    confirm: Literal[True]


class WindowArgs(_Args):
    orientation: Orientation
    widthM: float = Field(ge=0.3, le=6)  # noqa: N815
    heightM: float = Field(ge=0.3, le=4)  # noqa: N815
    sillM: float = Field(ge=0, le=2)  # noqa: N815
    depthM: float = Field(ge=0.03, le=0.5)  # noqa: N815
    spacingM: float = Field(ge=0.2, le=20)  # noqa: N815
    columns: int | None = Field(ge=1, le=100)
    floors: Floors | None
    floorHeightM: float | None = Field(gt=1.5, le=10)  # noqa: N815
    glassMaterialId: Literal["glass_blue"] | None  # noqa: N815
    frameMaterialId: Literal["metal_dark", "wood_exterior", "render_white"] | None  # noqa: N815


class BalconyArgs(_Args):
    orientation: Orientation
    widthM: float = Field(ge=0.8, le=20)  # noqa: N815
    projectionM: float = Field(ge=0.3, le=3)  # noqa: N815
    thicknessM: float = Field(ge=0.08, le=0.5)  # noqa: N815
    railingHeightM: float = Field(ge=0.9, le=1.5)  # noqa: N815
    floors: Floors | None
    floorHeightM: float | None = Field(gt=1.5, le=10)  # noqa: N815
    materialId: Literal["concrete_exposed", "metal_dark", "render_white", "stone_grey"] | None  # noqa: N815


class SetbackArgs(_Args):
    startHeightM: float = Field(ge=1, le=500)  # noqa: N815
    setbackM: float = Field(gt=0, le=50)  # noqa: N815
    orientations: list[Literal["north", "south", "east", "west", "all"]] = Field(min_length=1)
    upperHeightM: float = Field(ge=1, le=100)  # noqa: N815


class RaiseWallsArgs(_Args):
    deltaM: float = Field(ge=-100, le=100)  # noqa: N815


class RidgeArgs(_Args):
    ridgeHeightM: float = Field(ge=0.3, le=30)  # noqa: N815


class UniqueArgs(_Args):
    confirm: Literal[True]


SunTime = Annotated[
    str, StringConstraints(pattern=r"^(([01]\d|2[0-3]):[0-5]\d|sunrise|sunset|noon)$")
]
Day = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]


class ViewArgs(_Args):
    name: Annotated[str, StringConstraints(min_length=1, max_length=60)]
    preset: Literal["iso", "top", "north", "south", "east", "west"]
    time: SunTime | None
    date: Day | None


class SunArgs(_Args):
    time: SunTime
    date: Day | None


@dataclass(frozen=True)
class ObjectInfo:
    """Lo que una herramienta necesita saber de un objeto para decidir si es compatible."""

    id: str
    label: str
    layer: str
    kind: str
    instance: bool
    surfaces: tuple[str, ...]
    height: float | None
    floor_height: float | None
    flat_roof: bool


@dataclass(frozen=True)
class ToolSpec:
    name: str
    version: str
    title: str
    description: str
    executor: Literal["server", "viewer"]
    targets: Literal["required", "optional", "none"]
    compatible: str
    preconditions: tuple[str, ...]
    effects: str
    preview: str
    confirm: str
    undo: str
    args: type[_Args]
    # Motivo de incompatibilidad del objeto con estos argumentos, o None si es compatible
    check: Callable[[ObjectInfo, Any], str | None] = field(default=lambda o, a: None)

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "title": self.title,
            "description": self.description,
            "executor": self.executor,
            "targets": self.targets,
            "compatible": self.compatible,
            "preconditions": list(self.preconditions),
            "effects": self.effects,
            "preview": self.preview,
            "confirm": self.confirm,
            "undo": self.undo,
            "argsSchema": self.args.model_json_schema(),
        }


def _material_check(obj: ObjectInfo, args: MaterialArgs) -> str | None:
    if args.surface not in obj.surfaces:
        return f"no tiene la superficie {args.surface}"
    return None


def _height_check(obj: ObjectInfo, args: HeightArgs) -> str | None:
    if obj.kind != "building":
        return "no es un edificio"
    if obj.instance:
        return "es una copia de un bloque (cambiaría todas las copias)"
    if not obj.flat_roof:
        return "su cubierta no es plana"
    if obj.height is None:
        return "no tiene altura registrada"
    return None


def _building_check(obj: ObjectInfo, args: Any) -> str | None:
    if obj.kind not in ("building", "site"):
        return "no es un edificio ni un elemento del entorno (viales y terreno no se mueven)"
    return None


def _merged_building(obj: ObjectInfo, args: Any) -> str | None:
    if obj.kind != "building":
        return "no es un edificio"
    if obj.instance:
        return "es una copia de bloque: independízala antes (block.make_unique)"
    return None


def _flat_building(obj: ObjectInfo, args: Any) -> str | None:
    reason = _merged_building(obj, args)
    if reason:
        return reason
    if not obj.flat_roof:
        return "su cubierta no es plana"
    return None


def _sloped_building(obj: ObjectInfo, args: Any) -> str | None:
    reason = _merged_building(obj, args)
    if reason:
        return reason
    if obj.flat_roof:
        return "su cubierta es plana: no tiene cumbrera"
    return None


def _instance_check(obj: ObjectInfo, args: Any) -> str | None:
    return None if obj.instance else "no es una copia de bloque (ya es independiente)"


def _deletable(obj: ObjectInfo, args: Any) -> str | None:
    if obj.kind in ("site", "facade_element"):
        return None
    return "solo se eliminan elementos añadidos (entorno, ventanas, balcones)"


def _site_target(obj: ObjectInfo, args: Any) -> str | None:
    return None


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            "material.assign",
            "1",
            "Asignar material",
            "Asigna un material de la biblioteca a una superficie (fachada, patio, cubierta, "
            "zócalo, suelo) de los objetos, con color, rugosidad, tamaño de pieza y giro "
            "opcionales.",
            "server",
            "required",
            "Objetos que tengan esa superficie.",
            (
                "Material de la biblioteca vigente",
                "Las asignaciones manuales protegidas prevalecen",
            ),
            "Revisión nueva del plan de materiales y del GLB materializado; la geometría no "
            "cambia.",
            "GLB materializado temporal",
            "Revisión de materiales nueva",
            "Revisión anterior",
            MaterialArgs,
            _material_check,
        ),
        ToolSpec(
            "building.set_height",
            "1",
            "Cambiar altura",
            "Regenera la extrusión de un edificio de cubierta plana: sube o baja la coronación "
            "(muros y cubierta) sin escalar la planta ni el zócalo. Modos: altura absoluta o "
            "incremento en metros, o número de plantas con la altura de planta configurada.",
            "server",
            "required",
            "Edificios fusionados (no copias de bloque) con cubierta plana y altura registrada.",
            (
                "Plantas: altura de planta de la capa o floorHeightM dado por el usuario",
                "Altura final entre 1 y 1000 m",
            ),
            "Geometría nueva (revisión geométrica nueva); materiales e IDs se conservan.",
            "GLB temporal con la altura nueva",
            "Revisión nueva",
            "Revisión anterior",
            HeightArgs,
            _height_check,
        ),
        ToolSpec(
            "object.move",
            "1",
            "Mover",
            "Desplaza objetos en planta (dxM hacia el este, dyM hacia el norte del plano), sin "
            "cambiar su cota.",
            "server",
            "required",
            "Edificios fusionados y copias de bloque de edificio (la copia entera).",
            ("No se reapoya sobre terreno irregular",),
            "Geometría nueva; materiales e IDs se conservan.",
            "GLB temporal",
            "Revisión nueva",
            "Revisión anterior",
            MoveArgs,
            _building_check,
        ),
        ToolSpec(
            "object.rotate",
            "1",
            "Girar",
            "Gira objetos en planta alrededor de su centro (grados, antihorario visto desde "
            "arriba).",
            "server",
            "required",
            "Edificios fusionados y copias de bloque de edificio (incluidas escaladas y giradas).",
            (),
            "Geometría nueva; materiales e IDs se conservan.",
            "GLB temporal",
            "Revisión nueva",
            "Revisión anterior",
            RotateArgs,
            _building_check,
        ),
        ToolSpec(
            "object.duplicate",
            "1",
            "Duplicar",
            "Crea copias desplazadas (dxM este, dyM norte por copia) con IDs y etiquetas nuevas; "
            "las copias heredan los materiales del original.",
            "server",
            "required",
            "Edificios fusionados y copias de bloque de edificio.",
            ("Se avisa si una copia se solapa con otro edificio",),
            "Objetos nuevos en la geometría y en el plan de materiales.",
            "GLB temporal",
            "Revisión nueva",
            "Revisión anterior",
            DuplicateArgs,
            _building_check,
        ),
        ToolSpec(
            "site.place",
            "1",
            "Colocar vegetación o mobiliario",
            "Coloca árboles (de copa o de porte vertical), arbustos, bancos, farolas o papeleras "
            "GENÉRICOS: en fila a lo largo de un objeto (camino), alrededor de él (plaza) o en "
            "un punto relativo. Cantidad, separación, lado, escala y orientación (a lo largo, "
            "mirando al objeto o fija). Apoyo: terreno, superficie del objeto o una cota dada.",
            "server",
            "optional",
            "Fila/alrededor: un objeto de referencia; punto: ninguno.",
            (
                "El apoyo no se inventa: sin terreno ni superficie debajo hay que dar la cota",
                "Recursos procedurales propios, sin especies ni productos reales",
            ),
            "Elementos nuevos instanciados en la capa ENTORNO, con ID propio cada uno.",
            "GLB temporal",
            "Revisión nueva",
            "Revisión anterior",
            SitePlaceArgs,
            _site_target,
        ),
        ToolSpec(
            "object.delete",
            "1",
            "Eliminar elemento añadido",
            "Elimina elementos añadidos: vegetación, mobiliario, ventanas o balcones.",
            "server",
            "required",
            "Elementos del entorno y de fachada creados con Extrude.",
            ("No elimina edificios ni viales del plano",),
            "Geometría eliminada; el ID deja de existir en la revisión nueva.",
            "GLB temporal",
            "Revisión nueva",
            "Revisión anterior",
            DeleteArgs,
            _deletable,
        ),
        ToolSpec(
            "facade.add_windows",
            "1",
            "Añadir ventanas",
            "Añade ventanas (vidrio + carpintería) SOBRE las fachadas de esa orientación: anchura, "
            "altura, antepecho, profundidad, separación, columnas y plantas. No perfora el muro.",
            "server",
            "required",
            "Edificios fusionados (cubierta plana o inclinada) con fachada en esa orientación.",
            (
                "Altura de planta configurada o dada",
                "Caben en la fachada y en cada planta",
                "Sin ventanas previas en esa fachada y plantas",
            ),
            "Objetos nuevos en VENTANAS y CARPINTERIAS ligados al edificio y su fachada.",
            "GLB temporal",
            "Revisión nueva",
            "Revisión anterior",
            WindowArgs,
            _merged_building,
        ),
        ToolSpec(
            "facade.add_balconies",
            "1",
            "Añadir balcones",
            "Añade balcones (losa en vuelo + barandilla) centrados en las fachadas con esa "
            "orientación, en las plantas indicadas (1 o superiores).",
            "server",
            "required",
            "Edificios fusionados; fachada exterior con esa orientación.",
            ("Altura de planta configurada o dada", "Caben en la fachada", "No en planta baja"),
            "Objetos nuevos en BALCONES ligados al edificio y su fachada.",
            "GLB temporal",
            "Revisión nueva",
            "Revisión anterior",
            BalconyArgs,
            _merged_building,
        ),
        ToolSpec(
            "building.setback",
            "1",
            "Retranquear / ático",
            "Baja el edificio a la cota de inicio y crea encima un volumen cuya huella pierde "
            "una franja de `setbackM` junto a las fachadas indicadas (patios y formas cóncavas "
            "incluidos; puede dividirse en partes). No escala el edificio.",
            "server",
            "required",
            "Edificios fusionados de cubierta plana.",
            (
                "Cota de inicio entre 1 m y la altura actual",
                "El volumen superior no desaparece",
                "Sin ventanas/balcones por encima de la cota de inicio",
            ),
            "Geometría nueva (ático con ID propio, hereda materiales); el original baja.",
            "GLB temporal",
            "Revisión nueva",
            "Revisión anterior",
            SetbackArgs,
            _flat_building,
        ),
        ToolSpec(
            "building.raise_walls",
            "1",
            "Subir muros conservando la cubierta",
            "Alarga los muros `deltaM` y traslada la cubierta entera (plana o inclinada) sin "
            "deformarla.",
            "server",
            "required",
            "Edificios fusionados con cubierta.",
            ("Altura final ≥ 1 m", "Al bajar, sin ventanas/balcones por encima"),
            "Geometría nueva; materiales e IDs se conservan.",
            "GLB temporal",
            "Revisión nueva",
            "Revisión anterior",
            RaiseWallsArgs,
            _merged_building,
        ),
        ToolSpec(
            "roof.set_ridge",
            "1",
            "Cambiar altura de cumbrera",
            "Fija la altura de cumbrera sobre el alero escalando en vertical solo lo que está por "
            "encima del alero (cubierta y hastiales); alero y huella no cambian, así que la "
            "pendiente cambia en proporción.",
            "server",
            "required",
            "Edificios fusionados de cubierta inclinada.",
            ("Cumbrera entre 0,3 y 30 m",),
            "Geometría nueva; materiales e IDs se conservan.",
            "GLB temporal",
            "Revisión nueva",
            "Revisión anterior",
            RidgeArgs,
            _sloped_building,
        ),
        ToolSpec(
            "block.make_unique",
            "1",
            "Independizar copia de bloque",
            "Convierte ESTA copia de un bloque en geometría propia (posición, giro y escala "
            "aplicados), conservando IDs, etiquetas, superficies y materiales. Las demás copias no "
            "cambian. Después admite altura, retranqueo, ventanas… (no edita todas las copias).",
            "server",
            "required",
            "Copias de bloque (instancias).",
            (),
            "La copia sale del bloque y pasa a la capa como edificio propio.",
            "GLB temporal",
            "Revisión nueva",
            "Revisión anterior",
            UniqueArgs,
            _instance_check,
        ),
        ToolSpec(
            "view.create",
            "1",
            "Crear vista",
            "Crea una vista guardada del visor: encuadre (iso, planta o alzado desde un punto "
            "cardinal), opcionalmente centrada en los objetos, y hora solar (HH:MM, sunrise, "
            "sunset = 20 min antes del ocaso calculado para la ubicación, noon).",
            "viewer",
            "optional",
            "Cualquier objeto (para encuadrar) o ninguno.",
            ("Hora y fecha en la zona horaria del proyecto",),
            "Vista nueva en la pestaña Vistas y estado de sol aplicado.",
            "Cámara y sol aplicados sin guardar",
            "Vista guardada",
            "Se borra la vista",
            ViewArgs,
        ),
        ToolSpec(
            "scene.set_sun",
            "1",
            "Cambiar sol",
            "Cambia la hora (y la fecha) del sol del visor: HH:MM, sunrise, sunset o noon.",
            "viewer",
            "none",
            "—",
            (),
            "Estado de sol del visor.",
            "Sol aplicado sin confirmar",
            "Sol aplicado",
            "Sol anterior",
            SunArgs,
        ),
    )
}

# Capacidades que la IA debe reconocer y declarar como pendientes (no son herramientas).
PLANNED: dict[str, str] = {
    "facade.openings_cut": "Huecos reales en el muro (perforar; las ventanas se añaden encima)",
    "facade.plinth_geometry": "Zócalos con geometría propia (el material del zócalo sí existe)",
    "site.species": "Especies vegetales o modelos de mobiliario concretos",
    "roof.slope_change": "Cambiar pendiente por ángulo o rehacer la forma de una cubierta",
    "site.paths_terrain": "Caminos nuevos y edición del terreno",
    "material.pbr_maps": "Materiales PBR con mapas de normales/rugosidad propios",
    "material.new": "Materiales fuera de la biblioteca",
    "block.edit": "Editar el bloque (todas sus copias a la vez)",
    "reference.images": "Referencias visuales (imágenes) como entrada",
    "other": "Otra capacidad no prevista",
}


def tools_payload(library: MaterialLibrary = DEFAULT_LIBRARY) -> dict[str, Any]:
    return {
        "tools": [t.public() for t in TOOLS.values()],
        "planned": [{"id": k, "title": v} for k, v in PLANNED.items()],
        "library": library.version,
    }
