/**
 * Catálogo de objetos materializables leído del propio GLB (sin objects.json): ID persistente,
 * etiqueta, capa y superficies presentes. Mismo origen de verdad que `extrude materialize`
 * (pipeline/src/extrude/materialize.py → glb_catalog).
 */
import type { BufferAttribute, Mesh, Object3D } from 'three';

/** Claves de superficie de materials.json, por código de `_SURFACE` (surfaces.py). */
export const SURFACE_KEYS = [
  'generic',
  'facade_exterior',
  'facade_courtyard',
  'roof',
  'foundation',
  'ground',
  'underside',
] as const;
export type SurfaceKey = (typeof SURFACE_KEYS)[number];

export const SURFACE_NAMES: Record<SurfaceKey, string> = {
  generic: 'Genérica',
  facade_exterior: 'Fachada exterior',
  facade_courtyard: 'Fachada de patio',
  roof: 'Cubierta',
  foundation: 'Zócalo',
  ground: 'Suelo',
  underside: 'Cara inferior',
};

export interface CatalogObject {
  id: string;
  label: string;
  layer: string;
  kind: string;
  surfaces: Set<SurfaceKey>;
}

function attribute(mesh: Mesh, name: string): BufferAttribute | undefined {
  return (mesh.geometry?.attributes as Record<string, BufferAttribute> | undefined)?.[name];
}

/** (índice local, código de superficie) presentes en una malla. */
function pairs(mesh: Mesh): Set<string> {
  const index = attribute(mesh, '_object_id');
  const surface = attribute(mesh, '_surface');
  const found = new Set<string>();
  if (!index || !surface) return found;
  for (let i = 0; i < index.count; i++) {
    found.add(`${Math.round(index.getX(i))}:${Math.round(surface.getX(i))}`);
  }
  return found;
}

function meshesBelow(node: Object3D, stopAtBlocks: boolean): Mesh[] {
  const meshes: Mesh[] = [];
  const visit = (o: Object3D): void => {
    if (o !== node && stopAtBlocks && 'block' in o.userData) return;
    if ((o as Mesh).isMesh) meshes.push(o as Mesh);
    o.children.forEach(visit);
  };
  visit(node);
  return meshes;
}

function entry(
  catalog: Map<string, CatalogObject>,
  id: string,
  label: string,
  layer: string,
  kind: string,
): CatalogObject {
  let object = catalog.get(id);
  if (!object) {
    object = { id, label, layer, kind, surfaces: new Set() };
    catalog.set(id, object);
  }
  return object;
}

export function buildCatalog(root: Object3D): Map<string, CatalogObject> {
  const catalog = new Map<string, CatalogObject>();
  root.traverse((node) => {
    const data = node.userData as Record<string, unknown>;
    if (typeof data['layer'] !== 'string') return;
    const layer = data['layer'];
    const kind = typeof data['kind'] === 'string' ? data['kind'] : '';
    const table = data['objects'] as { id?: unknown[]; label?: unknown[] } | undefined;
    if (Array.isArray(table?.id)) {
      for (const mesh of meshesBelow(node, true)) {
        for (const pair of pairs(mesh)) {
          const [i, code] = pair.split(':').map(Number) as [number, number];
          const id = table.id[i];
          const key = SURFACE_KEYS[code];
          if (typeof id !== 'string' || !key) continue;
          const label = table.label?.[i];
          entry(catalog, id, typeof label === 'string' ? label : id, layer, kind).surfaces.add(key);
        }
      }
    }
    node.traverse((child) => {
      const block = child.userData as Record<string, unknown>;
      if (child === node || typeof block['block'] !== 'string') return;
      const instances = block['object_ids'];
      if (!Array.isArray(instances)) return;
      const found = new Set<string>();
      for (const mesh of meshesBelow(child, false)) pairs(mesh).forEach((p) => found.add(p));
      instances.forEach((ids: unknown, n: number) => {
        if (!Array.isArray(ids)) return;
        for (const pair of found) {
          const [part, code] = pair.split(':').map(Number) as [number, number];
          const id: unknown = ids[part];
          const key = SURFACE_KEYS[code];
          if (typeof id !== 'string' || !key) continue;
          const label = `${String(block['block'])} #${n + 1}`;
          entry(catalog, id, label, layer, kind).surfaces.add(key);
        }
      });
    });
  });
  return catalog;
}

/** Orden de lista: por etiqueta, en orden natural (E-2 antes que E-10). */
export function sortedObjects(catalog: Map<string, CatalogObject>): CatalogObject[] {
  return [...catalog.values()].sort((a, b) =>
    a.label.localeCompare(b.label, 'es', { numeric: true }),
  );
}
