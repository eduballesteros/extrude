import {
  BufferGeometry,
  Float32BufferAttribute,
  Group,
  InstancedMesh,
  Mesh,
  MeshStandardMaterial,
} from 'three';
import { describe, expect, it } from 'vitest';
import { buildCatalog, sortedObjects } from './catalog';

function geometry(objects: number[], surfaces: number[]): BufferGeometry {
  const g = new BufferGeometry();
  g.setAttribute('position', new Float32BufferAttribute(new Array(objects.length * 3).fill(0), 3));
  g.setAttribute('_object_id', new Float32BufferAttribute(objects, 1));
  g.setAttribute('_surface', new Float32BufferAttribute(surfaces, 1));
  return g;
}

describe('buildCatalog', () => {
  it('lee IDs, etiquetas y superficies de capas fusionadas y bloques instanciados', () => {
    const root = new Group();
    const layer = new Group();
    layer.userData = {
      layer: 'EDIFICIOS',
      kind: 'building',
      objects: { id: ['obj-b', 'obj-a'], label: ['E-002', 'E-001'] },
    };
    // Dos primitivas (como un GLB materializado): fachadas y cubiertas
    layer.add(new Mesh(geometry([0, 0, 1, 1], [1, 1, 1, 2]), new MeshStandardMaterial()));
    layer.add(new Mesh(geometry([0, 1], [3, 3]), new MeshStandardMaterial()));
    const block = new InstancedMesh(geometry([0, 0], [1, 3]), new MeshStandardMaterial(), 2);
    block.userData = { block: 'CASETA', instances: 2, object_ids: [['obj-c1'], ['obj-c2']] };
    layer.add(block);
    root.add(layer);

    const catalog = buildCatalog(root);
    expect([...catalog.get('obj-a')!.surfaces].sort()).toEqual([
      'facade_courtyard',
      'facade_exterior',
      'roof',
    ]);
    expect([...catalog.get('obj-b')!.surfaces].sort()).toEqual(['facade_exterior', 'roof']);
    expect(catalog.get('obj-c2')).toMatchObject({ label: 'CASETA #2', layer: 'EDIFICIOS' });
    expect([...catalog.get('obj-c1')!.surfaces].sort()).toEqual(['facade_exterior', 'roof']);
    expect(sortedObjects(catalog).map((o) => o.label)).toEqual([
      'CASETA #1',
      'CASETA #2',
      'E-001',
      'E-002',
    ]);
  });

  it('un GLB sin _surface (versión anterior) no ofrece objetos', () => {
    const root = new Group();
    const layer = new Group();
    layer.userData = { layer: 'E', kind: 'building', objects: { id: ['obj-a'] } };
    const g = new BufferGeometry();
    g.setAttribute('position', new Float32BufferAttribute([0, 0, 0], 3));
    layer.add(new Mesh(g, new MeshStandardMaterial()));
    root.add(layer);
    expect(buildCatalog(root).size).toBe(0);
  });
});
