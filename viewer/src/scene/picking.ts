import type { BufferAttribute, Intersection, Object3D, Vector3 } from 'three';

/** Información de un objeto seleccionado, a partir de los `extras` que escribe el pipeline. */
export interface PickInfo {
  point: Vector3;
  layer: string | null;
  kind: string | null;
  fields: [string, string][];
}

type ObjectTable = Record<string, unknown[]>;

function findAncestor(object: Object3D, key: string): Object3D | null {
  let current: Object3D | null = object;
  while (current) {
    if (key in current.userData) return current;
    current = current.parent;
  }
  return null;
}

function format(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2);
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
}

const FIELD_LABELS: Record<string, string> = {
  height: 'Altura (m)',
  height_origin: 'Origen de la altura',
  source_type: 'Tipo DXF',
  handle: 'Handle',
};

export function describeHit(hit: Intersection): PickInfo {
  const layerNode = findAncestor(hit.object, 'layer');
  const extras = (layerNode?.userData ?? {}) as Record<string, unknown>;
  const fields: [string, string][] = [];

  const blockNode = findAncestor(hit.object, 'block');
  if (blockNode && hit.instanceId !== undefined) {
    fields.push(['Bloque', format(blockNode.userData['block'])]);
    fields.push([
      'Instancia',
      `${hit.instanceId + 1} de ${format(blockNode.userData['instances'])}`,
    ]);
  } else if (hit.face) {
    const ids = (hit.object as { geometry?: { attributes: Record<string, BufferAttribute> } })
      .geometry?.attributes['_object_id'];
    const table = extras['objects'] as ObjectTable | undefined;
    if (ids && table) {
      const id = Math.round(ids.getX(hit.face.a));
      fields.push(['Objeto', String(id)]);
      for (const [key, values] of Object.entries(table)) {
        fields.push([FIELD_LABELS[key] ?? key, format(values[id])]);
      }
    }
  }
  if (extras['source_entities']) {
    const entities = extras['source_entities'] as Record<string, number>;
    fields.push([
      'Entidades de la capa',
      Object.entries(entities)
        .map(([t, n]) => `${t}×${n}`)
        .join(', '),
    ]);
  }
  const p = hit.point;
  fields.push(['Punto (escena)', `${p.x.toFixed(2)}, ${p.y.toFixed(2)}, ${p.z.toFixed(2)}`]);
  return {
    point: p.clone(),
    layer: typeof extras['layer'] === 'string' ? extras['layer'] : null,
    kind: typeof extras['kind'] === 'string' ? extras['kind'] : null,
    fields,
  };
}

/** Primer impacto visible (ignora capas ocultas y ayudas de la interfaz). */
export function firstVisibleHit(hits: Intersection[]): Intersection | null {
  for (const hit of hits) {
    let visible = true;
    let current: Object3D | null = hit.object;
    while (current) {
      if (!current.visible) {
        visible = false;
        break;
      }
      current = current.parent;
    }
    if (visible) return hit;
  }
  return null;
}
