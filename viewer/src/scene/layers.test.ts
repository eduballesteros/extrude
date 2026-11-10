import {
  BoxGeometry,
  Float32BufferAttribute,
  Group,
  InstancedMesh,
  Mesh,
  MeshStandardMaterial,
  Raycaster,
  Vector3,
} from 'three';
import { describe, expect, it } from 'vitest';
import { LayerManager } from './layers';
import { describeHit, firstVisibleHit } from './picking';

/** Escena con la misma forma que el GLB del pipeline: nodo de capa con extras. */
function pipelineLikeScene(): { root: Group; buildings: Mesh; trees: InstancedMesh } {
  const root = new Group();
  const geometry = new BoxGeometry(10, 10, 10);
  const ids = new Float32Array(geometry.attributes['position']!.count).fill(1);
  geometry.setAttribute('_object_id', new Float32BufferAttribute(ids, 1));
  const buildings = new Mesh(geometry, new MeshStandardMaterial({ color: '#d9d4c7' }));
  buildings.userData = {
    layer: 'EDIFICIOS',
    kind: 'building',
    visible: true,
    triangle_count: 12,
    objects: { height: [6, 12.5], height_origin: ['default_height', 'text'], handle: ['A1', 'B2'] },
  };
  const vegetation = new Group();
  vegetation.userData = { layer: 'VEGETACION', kind: 'vegetation', visible: false };
  const trees = new InstancedMesh(new BoxGeometry(1, 1, 1), new MeshStandardMaterial(), 2);
  trees.userData = { block: 'ARBOL', instances: 2 };
  vegetation.add(trees);
  root.add(buildings, vegetation);
  root.updateMatrixWorld(true);
  return { root, buildings, trees };
}

describe('LayerManager', () => {
  it('descubre capas, visibilidad inicial y colores originales', () => {
    const { root } = pipelineLikeScene();
    const manager = new LayerManager();
    manager.attach(root);
    expect([...manager.layers.keys()]).toEqual(['EDIFICIOS', 'VEGETACION']);
    expect(manager.initialState()).toEqual({
      EDIFICIOS: { visible: true },
      VEGETACION: { visible: false },
    });
    expect(manager.layers.get('EDIFICIOS')?.triangles).toBe(12);
  });

  it('aplica visibilidad y color, y restaura el original', () => {
    const { root, buildings } = pipelineLikeScene();
    const manager = new LayerManager();
    manager.attach(root);
    const state = manager.initialState();
    state['EDIFICIOS']!.colorOverride = '#ff0000';
    manager.apply(state);
    const material = buildings.material as MeshStandardMaterial;
    expect(material.color.getHexString()).toBe('ff0000');
    delete state['EDIFICIOS']!.colorOverride;
    manager.apply(state);
    expect(material.color.getHexString()).toBe('d9d4c7');
  });

  it('aislar y mostrar todas', () => {
    const state = { A: { visible: true }, B: { visible: true }, C: { visible: false } };
    LayerManager.isolate(state, 'B');
    expect(Object.values(state).map((s) => s.visible)).toEqual([false, true, false]);
    LayerManager.showAll(state);
    expect(Object.values(state).every((s) => s.visible)).toBe(true);
  });
});

describe('selección', () => {
  const ray = (root: Group, x: number, z: number) =>
    new Raycaster(new Vector3(x, 100, z), new Vector3(0, -1, 0)).intersectObject(root, true);

  it('lee los metadatos del objeto por _OBJECT_ID', () => {
    const { root } = pipelineLikeScene();
    const hit = firstVisibleHit(ray(root, 0, 0));
    const info = describeHit(hit!);
    expect(info.layer).toBe('EDIFICIOS');
    expect(Object.fromEntries(info.fields)).toMatchObject({
      Objeto: '1',
      'Altura (m)': '12.50',
      'Origen de la altura': 'text',
      Handle: 'B2',
    });
  });

  it('ignora capas ocultas', () => {
    const { root, buildings, trees } = pipelineLikeScene();
    buildings.visible = false;
    trees.parent!.visible = false;
    expect(firstVisibleHit(ray(root, 0, 0))).toBeNull();
  });

  it('identifica bloque e instancia', () => {
    const { root, trees, buildings } = pipelineLikeScene();
    buildings.visible = false;
    trees.parent!.visible = true;
    const hit = firstVisibleHit(ray(root, 0, 0));
    const fields = Object.fromEntries(describeHit(hit!).fields);
    expect(fields['Bloque']).toBe('ARBOL');
    expect(fields['Instancia']).toMatch(/de 2$/);
  });
});
