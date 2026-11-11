"""GLB base + materials.json → GLB materializado, autocontenido (Fase 1).

No vuelve a leer el DWG ni a construir geometría (RN-01): parte del GLB que genera `convert`,
que ya lleva por vértice el objeto (`_OBJECT_ID` + `extras.objects.id`), la superficie
(`_SURFACE`) y UV en metros (`TEXCOORD_0`).

- El plan se valida contra el propio GLB (revisión geométrica, IDs, superficies presentes) y la
  biblioteca antes de escribir nada: un plan inválido no produce un GLB parcial.
- Cada primitiva se divide por material (RG-07: agrupado por capa y material, no por edificio).
  Las superficies sin asignación conservan el material de su capa.
- Las copias instanciadas se reagrupan por asignación (RG-08): cambiar el material de una copia
  la saca a otro nodo instanciado, sin teñir las demás. Dentro de cada grupo, las UV se escalan
  con la escala de la copia (RG-06), así que las copias con distinta escala también se separan
  cuando llevan textura.
- Las UV en metros se convierten a UV de textura con la escala física y el giro del material
  (`tileSizeM`, `rotationDeg`), escritos directamente en `TEXCOORD_0`: no depende de
  KHR_texture_transform y Blender, Three.js u otro motor ven lo mismo.
- Las texturas (PNG procedurales de textures.py) van dentro del GLB (RF-16).
- Posiciones, normales, índices de objeto y metadatos se copian sin cambios (RG-10): solo se
  reparten vértices entre primitivas.
"""

from __future__ import annotations

import copy
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from extrude.errors import ExtrudeError
from extrude.gltf_export import (
    FLOAT,
    INSTANCING_EXT,
    MODE_TRIANGLES,
    UINT16,
    UINT32,
    _Builder,
    decode_glb,
    srgb_to_linear,
)
from extrude.material_library import DEFAULT_LIBRARY, MaterialLibrary, MaterialPreset
from extrude.materiality import Assignment, MaterialPlan, effective_assignments, validate_plan
from extrude.surfaces import Surface
from extrude.textures import texture

COMPONENTS: dict[int, Any] = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
COMPRESSION_EXTENSIONS = ("KHR_draco_mesh_compression", "EXT_meshopt_compression")
VERTICAL = {Surface.FACADE_EXTERIOR, Surface.FACADE_COURTYARD, Surface.FOUNDATION, Surface.GENERIC}
REPEAT, LINEAR, LINEAR_MIPMAP_LINEAR = 10497, 9729, 9987
SCALE_DIGITS = 6
MAX_REPORTED_ISSUES = 10

# Clave de material de un triángulo: índice de asignación del plan, o -1 - material de capa
Key = int


@dataclass(frozen=True)
class MaterializeStats:
    materials: int
    textures: int
    draw_calls: int
    assigned_triangles: int
    split_instance_nodes: int
    size_bytes: int


class GlbData:
    def __init__(self, data: bytes) -> None:
        self.doc, self.blob = decode_glb(data)

    def array(self, index: int) -> NDArray[Any]:
        acc = self.doc["accessors"][index]
        view = self.doc["bufferViews"][acc["bufferView"]]
        if view.get("byteStride"):
            raise ExtrudeError(
                "El GLB tiene datos intercalados (byteStride) que Extrude no genera.",
                "Materializa el model.glb original de extrude convert.",
            )
        dtype = COMPONENTS[acc["componentType"]]
        width = WIDTH[acc["type"]]
        start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        count = acc["count"] * width
        values = np.frombuffer(self.blob, dtype=dtype, count=count, offset=start)
        return values.reshape(-1, width) if width > 1 else values.copy()


def check_base(doc: dict[str, Any]) -> dict[str, Any]:
    used = set(doc.get("extensionsUsed", [])) | set(doc.get("extensionsRequired", []))
    if used & set(COMPRESSION_EXTENSIONS):
        raise ExtrudeError(
            "El GLB está comprimido (Draco/Meshopt).",
            "Materializa el GLB sin comprimir de extrude convert y usa --compress al materializar.",
        )
    extras: dict[str, Any] = doc["scenes"][doc.get("scene", 0)].get("extras", {})
    if extras.get("material_revision"):
        raise ExtrudeError(
            "Este GLB ya está materializado.",
            "Aplica el plan sobre el model.glb original: cada materialización parte de él.",
        )
    if not extras.get("geometry_revision"):
        raise ExtrudeError(
            "El GLB no tiene revisión geométrica (versión anterior de Extrude).",
            "Vuelve a convertir el DWG/DXF con esta versión.",
        )
    return extras


def _triangle_prims(glb: GlbData, mesh: int) -> list[dict[str, Any]]:
    return [
        p
        for p in glb.doc["meshes"][mesh]["primitives"]
        if p.get("mode", MODE_TRIANGLES) == MODE_TRIANGLES and "_SURFACE" in p["attributes"]
    ]


def glb_catalog(glb: GlbData, revision: str) -> dict[str, Any]:
    """Catálogo mínimo (IDs, etiquetas, superficies) deducido del propio GLB."""
    surfaces: dict[str, set[str]] = defaultdict(set)
    labels: dict[str, str] = {}
    for node in glb.doc["nodes"]:
        extras = node.get("extras", {})
        if "mesh" not in node:
            continue
        for prim in _triangle_prims(glb, node["mesh"]):
            index = np.rint(glb.array(prim["attributes"]["_OBJECT_ID"])).astype(np.int64)
            surface = np.rint(glb.array(prim["attributes"]["_SURFACE"])).astype(np.int64)
            pairs = {
                (int(i), int(s)) for i, s in set(zip(index.tolist(), surface.tolist(), strict=True))
            }
            table = extras.get("objects", {})
            if INSTANCING_EXT in node.get("extensions", {}):
                for ids in extras.get("object_ids", []):
                    for part, code in pairs:
                        if part < len(ids) and ids[part]:
                            surfaces[ids[part]].add(Surface(code).key)
            elif "id" in table:
                for i, code in pairs:
                    object_id = table["id"][i]
                    if object_id:
                        surfaces[object_id].add(Surface(code).key)
                        label = table.get("label", [None] * (i + 1))[i]
                        if label:
                            labels[object_id] = label
    return {
        "geometryRevision": revision,
        "objects": [
            {"id": i, "label": labels.get(i), "surfaces": {s: {} for s in sorted(found)}}
            for i, found in surfaces.items()
        ],
    }


class _Materializer:
    def __init__(self, glb: GlbData, plan: MaterialPlan, library: MaterialLibrary) -> None:
        self.glb = glb
        self.plan = plan
        self.library = library
        self.builder = _Builder()
        self.table = effective_assignments(plan)
        self.index_of = {id(a): i for i, a in enumerate(plan.assignments)}
        self.layer_materials: dict[int, int] = {}
        self.plan_materials: dict[tuple[str, str, str, float], int] = {}
        self.textures: dict[str, int] = {}
        self.assigned_triangles = 0
        self.split_nodes = 0

    # --- materiales -------------------------------------------------------------------------
    def layer_material(self, original: int) -> int:
        if original not in self.layer_materials:
            self.builder.doc["materials"].append(copy.deepcopy(self.glb.doc["materials"][original]))
            self.layer_materials[original] = len(self.builder.doc["materials"]) - 1
        return self.layer_materials[original]

    def preset(self, item: Assignment) -> MaterialPreset:
        preset = self.library.get(item.material_id, item.material_version)
        if preset is None:  # validate_plan ya lo descartó
            raise ValueError(f"Material desconocido: {item.material_id}")
        return preset

    def texture_index(self, pattern: str) -> int:
        if pattern not in self.textures:
            png, _ = texture(pattern)
            doc = self.builder.doc
            view = self.builder.view(png, None)
            doc.setdefault("images", []).append(
                {"name": pattern, "mimeType": "image/png", "bufferView": view}
            )
            if not doc.get("samplers"):
                doc["samplers"] = [
                    {
                        "magFilter": LINEAR,
                        "minFilter": LINEAR_MIPMAP_LINEAR,
                        "wrapS": REPEAT,
                        "wrapT": REPEAT,
                    }
                ]
            doc.setdefault("textures", []).append({"sampler": 0, "source": len(doc["images"]) - 1})
            self.textures[pattern] = len(doc["textures"]) - 1
        return self.textures[pattern]

    def plan_material(self, item: Assignment) -> int:
        preset = self.preset(item)
        color = (item.parameters.color or preset.color).lower()
        roughness = (
            item.parameters.roughness if item.parameters.roughness is not None else preset.roughness
        )
        key = (preset.id, preset.version, color, roughness)
        if key in self.plan_materials:
            return self.plan_materials[key]
        linear = srgb_to_linear(color)
        pbr: dict[str, Any] = {"metallicFactor": preset.metalness, "roughnessFactor": roughness}
        if preset.pattern:
            _, mean = texture(preset.pattern)
            # La textura es luminancia: se compensa su media para conservar el color medio, con
            # el mismo factor en los tres canales (recortar canal a canal cambiaría el tono)
            gain = min(1 / mean, 1 / max(max(linear), 1e-6))
            linear = [c * gain for c in linear]
            pbr["baseColorTexture"] = {"index": self.texture_index(preset.pattern)}
        pbr["baseColorFactor"] = [*linear, preset.opacity]
        material: dict[str, Any] = {
            "name": preset.name,
            "pbrMetallicRoughness": pbr,
            "extras": {
                "material_id": preset.id,
                "material_version": preset.version,
                "color": color,
                "material_revision": self.plan.material_revision,
            },
        }
        if preset.opacity < 1:
            material["alphaMode"] = "BLEND"
        self.builder.doc["materials"].append(material)
        self.plan_materials[key] = len(self.builder.doc["materials"]) - 1
        return self.plan_materials[key]

    def resolve(self, key: Key) -> int:
        if key >= 0:
            return self.plan_material(self.plan.assignments[key])
        return self.layer_material(-1 - key)

    def key(self, object_id: str | None, surface: int, layer_material: int) -> Key:
        if object_id:
            item = self.table.get((object_id, Surface(surface).key))
            if item is not None:
                return self.index_of[id(item)]
        return -1 - layer_material

    # --- UV -----------------------------------------------------------------------------------
    def texture_uvs(
        self,
        uvs: NDArray[np.float32],
        surfaces: NDArray[np.int64],
        key: Key,
        scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
    ) -> NDArray[np.float32]:
        """UV en metros → UV de textura. `scale` = (sx, sy, sz) de la copia (ejes del dibujo)."""
        if key < 0:
            return uvs
        item = self.plan.assignments[key]
        preset = self.preset(item)
        width, height = item.parameters.tile_size_m or preset.tile_size_m
        metric = uvs.astype(np.float64)
        sx, sy, sz = scale
        vertical = np.isin(surfaces, [int(s) for s in VERTICAL])
        metric[:, 0] *= sx
        metric[:, 1] *= np.where(vertical, sz, sy)
        angle = math.radians(item.parameters.rotation_deg or 0.0)
        if angle:
            c, s = math.cos(angle), math.sin(angle)
            metric = metric @ np.array([[c, -s], [s, c]])
        # glTF: v = 0 es la fila superior de la imagen; la textura se dibuja con v hacia arriba
        result = np.column_stack([metric[:, 0] / width, -metric[:, 1] / height])
        return result.astype(np.float32)

    # --- primitivas ---------------------------------------------------------------------------
    def split(
        self,
        prim: dict[str, Any],
        keys_of: Any,
        scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
    ) -> list[dict[str, Any]]:
        """Divide una primitiva por material. `keys_of(object_index, surface) → Key`."""
        glb = self.glb
        attributes = {name: glb.array(i) for name, i in prim["attributes"].items()}
        faces = glb.array(prim["indices"]).astype(np.int64).reshape(-1, 3)
        objects = np.rint(attributes["_OBJECT_ID"]).astype(np.int64)
        surfaces = np.rint(attributes["_SURFACE"]).astype(np.int64)
        first = faces[:, 0]
        pair_keys = {
            pair: keys_of(*pair)
            for pair in set(zip(objects[first].tolist(), surfaces[first].tolist(), strict=True))
        }
        triangle_keys = np.array(
            [
                pair_keys[(o, s)]
                for o, s in zip(objects[first].tolist(), surfaces[first].tolist(), strict=True)
            ],
            dtype=np.int64,
        )
        out = []
        for key in sorted(set(triangle_keys.tolist())):
            subset = faces[triangle_keys == key]
            used = np.unique(subset)
            remapped = np.searchsorted(used, subset)
            if key >= 0:
                self.assigned_triangles += len(subset)
            new_attributes: dict[str, int] = {}
            for name, values in attributes.items():
                part = values[used]
                if name == "TEXCOORD_0":
                    part = self.texture_uvs(part, surfaces[used], key, scale)
                width = part.shape[1] if part.ndim > 1 else 1
                kind = {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4"}[width]
                new_attributes[name] = self.builder.accessor(
                    np.ascontiguousarray(part, dtype=np.float32),
                    FLOAT,
                    kind,
                    bounds=name == "POSITION",
                )
            out.append(
                {
                    "attributes": new_attributes,
                    "indices": self.builder.indices(remapped, len(used)),
                    "material": self.resolve(key),
                    "mode": MODE_TRIANGLES,
                }
            )
        return out

    def copy_primitive(self, prim: dict[str, Any]) -> dict[str, Any]:
        attributes = {}
        for name, index in prim["attributes"].items():
            values = self.glb.array(index)
            acc = self.glb.doc["accessors"][index]
            attributes[name] = self.builder.accessor(
                values, acc["componentType"], acc["type"], bounds="min" in acc
            )
        new = dict(prim, attributes=attributes)
        if "indices" in prim:
            acc = self.glb.doc["accessors"][prim["indices"]]
            values = self.glb.array(prim["indices"])
            component = UINT16 if acc["componentType"] == UINT16 else UINT32
            new["indices"] = self.builder.accessor(
                values.astype(np.uint16 if component == UINT16 else np.uint32),
                component,
                "SCALAR",
                target=34963,
            )
        if "material" in prim:
            new["material"] = self.layer_material(prim["material"])
        return new

    # --- nodos --------------------------------------------------------------------------------
    def run(self) -> bytes:
        doc = self.glb.doc
        nodes = copy.deepcopy(doc["nodes"])
        self.builder.doc["nodes"] = nodes
        extra_children: dict[int, list[int]] = defaultdict(list)
        parent = {c: i for i, n in enumerate(doc["nodes"]) for c in n.get("children", [])}
        for index, node in enumerate(doc["nodes"]):
            if "mesh" not in node:
                continue
            if INSTANCING_EXT in node.get("extensions", {}):
                siblings = self.instanced(index, node, nodes)
                if index in parent:
                    extra_children[parent[index]].extend(siblings)
                continue
            table = node.get("extras", {}).get("objects", {})
            ids = table.get("id", [])
            prims = []
            for prim in doc["meshes"][node["mesh"]]["primitives"]:
                if (
                    prim in _triangle_prims(self.glb, node["mesh"])
                    and "_OBJECT_ID" in prim["attributes"]
                ):
                    material = prim.get("material", 0)
                    prims.extend(
                        self.split(
                            prim,
                            lambda o, s, m=material, ids=ids: self.key(
                                ids[o] if o < len(ids) else None, s, m
                            ),
                        )
                    )
                else:
                    prims.append(self.copy_primitive(prim))
            nodes[index]["mesh"] = self.builder.mesh(doc["meshes"][node["mesh"]]["name"], prims)
        for parent_index, children in extra_children.items():
            nodes[parent_index].setdefault("children", []).extend(children)

        self.builder.doc["scenes"] = copy.deepcopy(doc["scenes"])
        self.builder.doc["scene"] = doc.get("scene", 0)
        for key in ("extensionsUsed", "extensionsRequired"):
            if key in doc:
                self.builder.doc[key] = list(doc[key])
        scene_extras = dict(doc["scenes"][doc.get("scene", 0)].get("extras", {}))
        scene_extras.update(
            {
                "material_revision": self.plan.material_revision,
                "material_project": self.plan.project_id,
                "material_catalog": self.plan.catalog_version,
                "material_assignments": len(self.plan.assignments),
                "uv_convention": "TEXCOORD_0 en UV de textura (escala física aplicada)",
            }
        )
        for empty in ("images", "textures", "samplers"):
            if empty in self.builder.doc and not self.builder.doc[empty]:
                del self.builder.doc[empty]
        return self.builder.encode(scene_extras)

    def instanced(self, index: int, node: dict[str, Any], nodes: list[dict[str, Any]]) -> list[int]:
        """Reparte las copias por asignación (y escala, si llevan textura). Devuelve los nodos
        nuevos que se añaden como hermanos del original."""
        glb = self.glb
        ext = node["extensions"][INSTANCING_EXT]["attributes"]
        trs = {name: glb.array(i) for name, i in ext.items()}
        count = len(next(iter(trs.values())))
        object_ids: list[list[str | None]] = node.get("extras", {}).get("object_ids") or [
            [] for _ in range(count)
        ]
        prims = glb.doc["meshes"][node["mesh"]]["primitives"]
        triangle_prims = _triangle_prims(glb, node["mesh"])
        pairs: list[tuple[int, int, int]] = []  # (primitiva, parte, superficie)
        for p_index, prim in enumerate(prims):
            if prim not in triangle_prims:
                continue
            parts = np.rint(glb.array(prim["attributes"]["_OBJECT_ID"])).astype(np.int64)
            surfaces = np.rint(glb.array(prim["attributes"]["_SURFACE"])).astype(np.int64)
            pairs.extend(
                (p_index, int(a), int(b))
                for a, b in sorted(set(zip(parts.tolist(), surfaces.tolist(), strict=True)))
            )
        # Escala por copia en ejes del dibujo: glTF (x, y, z) = dibujo (x, z, -y)
        scales = trs.get("SCALE", np.ones((count, 3)))
        groups: dict[tuple[Any, ...], list[int]] = defaultdict(list)
        for i in range(count):
            ids = object_ids[i] if i < len(object_ids) else []
            keys = tuple(
                self.key(
                    ids[part] if part < len(ids) else None, surface, prims[p].get("material", 0)
                )
                for p, part, surface in pairs
            )
            textured = any(
                k >= 0 and self.preset(self.plan.assignments[k]).pattern is not None for k in keys
            )
            s = scales[i]
            scale = (
                (
                    round(float(s[0]), SCALE_DIGITS),
                    round(float(s[2]), SCALE_DIGITS),
                    round(float(s[1]), SCALE_DIGITS),
                )
                if textured
                else None
            )
            groups[(keys, scale)].append(i)

        siblings: list[int] = []
        for n, ((keys, scale), members) in enumerate(groups.items()):
            key_of = {
                (p, part, surface): k for (p, part, surface), k in zip(pairs, keys, strict=True)
            }
            new_prims = []
            for p_index, prim in enumerate(prims):
                if prim in triangle_prims:
                    layer_material = prim.get("material", 0)
                    new_prims.extend(
                        self.split(
                            prim,
                            lambda o, s, p=p_index, k=key_of, m=layer_material: k.get(
                                (p, o, s), -1 - m
                            ),
                            scale or (1.0, 1.0, 1.0),
                        )
                    )
                else:
                    new_prims.append(self.copy_primitive(prim))
            target = copy.deepcopy(node) if n else nodes[index]
            target["mesh"] = self.builder.mesh(glb.doc["meshes"][node["mesh"]]["name"], new_prims)
            target["extensions"][INSTANCING_EXT]["attributes"] = {
                name: self.builder.accessor(
                    np.ascontiguousarray(values[members]),
                    FLOAT,
                    {3: "VEC3", 4: "VEC4"}[values.shape[1]],
                    None,
                )
                for name, values in trs.items()
            }
            extras = target.setdefault("extras", {})
            extras["instances"] = len(members)
            if node.get("extras", {}).get("object_ids"):
                extras["object_ids"] = [object_ids[i] for i in members]
            if n:
                nodes.append(target)
                siblings.append(len(nodes) - 1)
                self.split_nodes += 1
        return siblings


def materialize(
    data: bytes, plan: MaterialPlan, library: MaterialLibrary = DEFAULT_LIBRARY
) -> tuple[bytes, MaterializeStats]:
    glb = GlbData(data)
    extras = check_base(glb.doc)
    catalog = glb_catalog(glb, extras["geometry_revision"])
    if not catalog["objects"]:
        raise ExtrudeError(
            "El GLB no tiene superficies ni UV por objeto (_SURFACE, TEXCOORD_0).",
            "Vuelve a convertir el DWG/DXF con esta versión de Extrude y materializa ese GLB.",
        )
    issues = validate_plan(plan, catalog, library)
    if issues:
        listed = "; ".join(i.message for i in issues[:MAX_REPORTED_ISSUES])
        more = len(issues) - MAX_REPORTED_ISSUES
        raise ExtrudeError(
            f"El plan no se puede aplicar a este GLB ({len(issues)} problemas): {listed}"
            + (f"; y {more} más" if more > 0 else ""),
            "Corrige materials.json (extrude validate-materials) o usa el GLB de su revisión.",
        )
    worker = _Materializer(glb, plan, library)
    out = worker.run()
    stats = MaterializeStats(
        materials=len(worker.builder.doc.get("materials", [])),
        textures=len(worker.textures),
        draw_calls=worker.builder.draw_calls,
        assigned_triangles=worker.assigned_triangles,
        split_instance_nodes=worker.split_nodes,
        size_bytes=len(out),
    )
    return out, stats
