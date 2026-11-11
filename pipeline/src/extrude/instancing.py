"""Detección de bloques repetidos aptos para `EXT_mesh_gpu_instancing`.

Una "instancia" es el conjunto de objetos de una capa que proceden de un mismo INSERT de primer
nivel. Un bloque se instancia solo si:
  - tiene al menos `min_count` instancias en la capa;
  - cada instancia, llevada a coordenadas del bloque con la inversa de su matriz, coincide con
    el prototipo: mismos vértices dentro de la tolerancia o, si las curvas se aplanaron con otra
    densidad (INSERT escalados), misma caja envolvente y misma área de superficie (±1 %). Así,
    bloques cuya geometría depende del INSERT (alturas por atributo, contornos que se fusionan
    con otros) no se instancian;
  - la matriz no refleja (determinante > 0) y se descompone en TRS sin cizalla;
  - escala igual en X e Y (en planta). El prototipo guarda UV en metros *locales* del bloque;
    al materializar se multiplican por la escala de cada copia para que un INSERT al doble de
    tamaño no duplique el tamaño del ladrillo (RG-06).
El resto de objetos sigue el camino normal (fusión por capa).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from extrude.build import SceneObject
from extrude.extrusion import MeshData, concat, triangle_areas

Matrix = NDArray[np.float64]  # 4×4, convención columna (p' = M @ p)
EQUIVALENT_AREA_RTOL = 0.01
PLANAR_EPS = 1e-9


@dataclass(frozen=True, slots=True)
class InstanceGroup:
    block: str
    prototype: MeshData  # coordenadas del bloque, metros, Z-up
    hard_edges: bool
    matrices: list[Matrix]  # bloque (metros) → WCS (metros)
    objects_per_instance: int
    # IDs persistentes (catalog) de los objetos de cada instancia, paralelo a `matrices`
    object_ids: list[tuple[str | None, ...]] = field(default_factory=list)
    # Parte (objeto dentro de la copia) de cada cara del prototipo
    face_parts: NDArray[np.int32] = field(default_factory=lambda: np.zeros(0, dtype=np.int32))


def block_matrix(raw: tuple[float, ...], scale: float) -> Matrix:
    """Matriz ezdxf (fila, unidades del dibujo) → columna en metros, con prototipo en metros."""
    m = np.array(raw, dtype=np.float64).reshape(4, 4).T
    units = np.diag([scale, scale, scale, 1.0])
    inv_units = np.diag([1 / scale, 1 / scale, 1 / scale, 1.0])
    result: Matrix = units @ m @ inv_units
    return result


def transform(points: NDArray[np.float64], matrix: Matrix) -> NDArray[np.float64]:
    out: NDArray[np.float64] = points @ matrix[:3, :3].T + matrix[:3, 3]
    return out


def decompose_trs(matrix: Matrix, tolerance: float = 1e-6) -> tuple[Matrix, Matrix, Matrix] | None:
    """(traslación, cuaternión xyzw, escala) o None si hay reflexión o cizalla."""
    linear = matrix[:3, :3]
    scale = np.linalg.norm(linear, axis=0)
    if np.any(scale < 1e-12) or np.linalg.det(linear) <= 0:
        return None
    rotation = linear / scale
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=tolerance):
        return None
    return matrix[:3, 3].copy(), _quaternion(rotation), scale


def _quaternion(r: Matrix) -> Matrix:
    trace = np.trace(r)
    if trace > 0:
        s = 2.0 * np.sqrt(trace + 1.0)
        q = [(r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s, 0.25 * s]
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = 2.0 * np.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2])
        q = [0.25 * s, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s, (r[2, 1] - r[1, 2]) / s]
    elif r[1, 1] > r[2, 2]:
        s = 2.0 * np.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2])
        q = [(r[0, 1] + r[1, 0]) / s, 0.25 * s, (r[1, 2] + r[2, 1]) / s, (r[0, 2] - r[2, 0]) / s]
    else:
        s = 2.0 * np.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1])
        q = [(r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, 0.25 * s, (r[1, 0] - r[0, 1]) / s]
    quat = np.array(q)
    result: Matrix = quat / np.linalg.norm(quat)
    return result


def plan_instances(
    objects: Sequence[SceneObject], *, scale: float, min_count: int, tolerance: float
) -> tuple[list[InstanceGroup], list[SceneObject]]:
    if min_count <= 0:
        return [], list(objects)
    by_insert: dict[str, list[SceneObject]] = defaultdict(list)
    loose: list[SceneObject] = []
    for obj in objects:
        if obj.instance is None:
            loose.append(obj)
        else:
            by_insert[obj.instance.handle].append(obj)

    by_block: dict[str, list[list[SceneObject]]] = defaultdict(list)
    for members in by_insert.values():
        by_block[members[0].instance.block].append(members)  # type: ignore[union-attr]

    groups: list[InstanceGroup] = []
    for block, instances in by_block.items():
        group, rejected = (
            _try_group(block, instances, scale, tolerance)
            if len(instances) >= min_count
            else (None, instances)
        )
        if group is not None and len(group.matrices) >= min_count:
            groups.append(group)
            loose.extend(obj for members in rejected for obj in members)
        else:
            loose.extend(obj for members in instances for obj in members)
    return groups, loose


def _try_group(
    block: str, instances: list[list[SceneObject]], scale: float, tolerance: float
) -> tuple[InstanceGroup | None, list[list[SceneObject]]]:
    """Agrupa las copias compatibles con la primera válida; devuelve también las rechazadas."""
    matrices: list[Matrix] = []
    face_parts = np.zeros(0, dtype=np.int32)
    object_ids: list[tuple[str | None, ...]] = []
    rejected: list[list[SceneObject]] = []
    prototype: MeshData | None = None
    reference: NDArray[np.float64] | None = None
    shape: tuple[int, bool] | None = None
    for members in instances:
        ref = members[0].instance
        matrix = block_matrix(ref.matrix, scale) if ref is not None else None
        hard = {o.hard_edges for o in members}
        if matrix is not None and hard == {False}:
            matrix = _extrusion_matrix(matrix)
        if matrix is None or decompose_trs(matrix) is None or len(hard) != 1:
            rejected.append(members)  # reflejada, con cizalla o mezcla de tipos
            continue
        own_scale = np.linalg.norm(matrix[:3, :3], axis=0)
        if not np.isclose(own_scale[0], own_scale[1], rtol=1e-6, atol=0):
            rejected.append(members)  # escala no uniforme en planta: UV de pared ambiguas
            continue
        mesh = concat([o.mesh for o in members])
        local = transform(mesh.vertices, np.linalg.inv(matrix))
        signature = _signature(local, tolerance)
        if reference is None:
            prototype = MeshData(
                local, mesh.faces, mesh.surfaces, _local_uvs(mesh, local, own_scale)
            )
            face_parts = np.repeat(
                np.arange(len(members), dtype=np.int32), [o.mesh.triangle_count for o in members]
            )
            reference = signature
            shape = (len(members), hard.pop())
        elif (len(members), hard.pop()) != shape or not (
            _same(signature, reference, tolerance)
            or _equivalent(MeshData(local, mesh.faces), prototype, tolerance)  # type: ignore[arg-type]
        ):
            rejected.append(members)
            continue
        matrices.append(matrix)
        object_ids.append(tuple(o.object_id for o in members))
    if prototype is None or shape is None:
        return None, instances
    group = InstanceGroup(block, prototype, shape[1], matrices, shape[0], object_ids, face_parts)
    return group, rejected


def _local_uvs(
    mesh: MeshData, local: NDArray[np.float64], scale: NDArray[np.float64]
) -> NDArray[np.float64] | None:
    """UV de pared en metros del bloque: distancia sobre el contorno / escala en planta y cota
    local. Las filas NaN (tapas) se proyectan en planta al exportar, también en local."""
    if mesh.uvs is None:
        return None
    uvs = mesh.uvs.copy()
    walls = np.isfinite(uvs[:, 0])
    uvs[walls, 0] /= scale[0]
    uvs[walls, 1] = local[walls, 2]
    return uvs


def _extrusion_matrix(matrix: Matrix) -> Matrix:
    """Para huellas 2D extruidas: la altura está en metros reales y no escala con el INSERT.

    Si el INSERT solo gira alrededor de Z y escala en XY (lo normal en planta), la instancia
    usa escala 1 en Z para que el prototipo conserve su altura real. Si no es plano, se deja
    la matriz completa (y la comprobación de forma decidirá).
    """
    planar = np.allclose(matrix[2, :2], 0, atol=PLANAR_EPS) and np.allclose(
        matrix[:2, 2], 0, atol=PLANAR_EPS
    )
    if not planar or matrix[2, 2] <= 0:
        return matrix
    fixed = matrix.copy()
    fixed[2, 2] = 1.0
    return fixed


def _equivalent(a: MeshData, b: MeshData, tolerance: float) -> bool:
    """Misma forma aunque con distinta densidad de vértices (curvas aplanadas a otra escala):
    caja envolvente igual (± tolerancia) y área de superficie igual (± EQUIVALENT_AREA_RTOL).
    Una altura distinta o un contorno fusionado con un vecino cambian una de las dos."""
    lo_a, hi_a = a.vertices.min(axis=0), a.vertices.max(axis=0)
    lo_b, hi_b = b.vertices.min(axis=0), b.vertices.max(axis=0)
    if not (np.allclose(lo_a, lo_b, atol=tolerance) and np.allclose(hi_a, hi_b, atol=tolerance)):
        return False
    area_a = float(triangle_areas(a.vertices, a.faces).sum())
    area_b = float(triangle_areas(b.vertices, b.faces).sum())
    return abs(area_a - area_b) <= EQUIVALENT_AREA_RTOL * max(area_a, area_b)


def _same(a: NDArray[np.float64], b: NDArray[np.float64], tolerance: float) -> bool:
    return a.shape == b.shape and bool(np.allclose(a, b, atol=tolerance))


def _signature(vertices: NDArray[np.float64], tolerance: float) -> NDArray[np.float64]:
    """Vértices únicos ordenados: independiente del orden de triangulación."""
    rounded = np.round(vertices / tolerance) * tolerance
    unique: NDArray[np.float64] = np.unique(rounded, axis=0)
    return unique
