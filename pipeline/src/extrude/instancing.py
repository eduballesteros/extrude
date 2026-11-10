"""Detección de bloques repetidos aptos para `EXT_mesh_gpu_instancing`.

Una "instancia" es el conjunto de objetos de una capa que proceden de un mismo INSERT de primer
nivel. Un bloque se instancia solo si:
  - tiene al menos `min_count` instancias en la capa;
  - cada instancia, llevada a coordenadas del bloque con la inversa de su matriz, coincide con
    el prototipo (mismos vértices dentro de la tolerancia). Así, bloques cuya geometría depende
    del INSERT (alturas por atributo, contornos que se fusionan con otros) no se instancian;
  - la matriz no refleja (determinante > 0) y se descompone en TRS sin cizalla.
El resto de objetos sigue el camino normal (fusión por capa).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from extrude.build import SceneObject
from extrude.extrusion import MeshData, concat

Matrix = NDArray[np.float64]  # 4×4, convención columna (p' = M @ p)


@dataclass(frozen=True, slots=True)
class InstanceGroup:
    block: str
    prototype: MeshData  # coordenadas del bloque, metros, Z-up
    hard_edges: bool
    matrices: list[Matrix]  # bloque (metros) → WCS (metros)
    objects_per_instance: int


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
    rejected: list[list[SceneObject]] = []
    prototype: MeshData | None = None
    reference: NDArray[np.float64] | None = None
    shape: tuple[int, bool] | None = None
    for members in instances:
        ref = members[0].instance
        matrix = block_matrix(ref.matrix, scale) if ref is not None else None
        hard = {o.hard_edges for o in members}
        if matrix is None or decompose_trs(matrix) is None or len(hard) != 1:
            rejected.append(members)  # reflejada, con cizalla o mezcla de tipos
            continue
        mesh = concat([o.mesh for o in members])
        local = transform(mesh.vertices, np.linalg.inv(matrix))
        signature = _signature(local, tolerance)
        if reference is None:
            prototype, reference = MeshData(local, mesh.faces), signature
            shape = (len(members), hard.pop())
        elif (len(members), hard.pop()) != shape or not _same(signature, reference, tolerance):
            rejected.append(members)
            continue
        matrices.append(matrix)
    if prototype is None or shape is None:
        return None, instances
    return InstanceGroup(block, prototype, shape[1], matrices, shape[0]), rejected


def _same(a: NDArray[np.float64], b: NDArray[np.float64], tolerance: float) -> bool:
    return a.shape == b.shape and bool(np.allclose(a, b, atol=tolerance))


def _signature(vertices: NDArray[np.float64], tolerance: float) -> NDArray[np.float64]:
    """Vértices únicos ordenados: independiente del orden de triangulación."""
    rounded = np.round(vertices / tolerance) * tolerance
    unique: NDArray[np.float64] = np.unique(rounded, axis=0)
    return unique
