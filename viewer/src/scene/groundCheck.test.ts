import { BoxGeometry, Group, Mesh, MeshStandardMaterial } from 'three';
import { describe, expect, it } from 'vitest';
import { type GroundCheck, misplacedLayers, misplacedMessage } from './groundCheck';

/** Nodo de capa como los del GLB del pipeline: caja de 10 m cuyo Y mínimo es `minY`. */
function layer(name: string, kind: string, minY: number, ground?: GroundCheck): Group {
  const node = new Group();
  node.userData = { layer: name, kind, ...(ground ? { ground_check: ground } : {}) };
  const mesh = new Mesh(new BoxGeometry(10, 10, 10), new MeshStandardMaterial());
  mesh.position.y = minY + 5;
  node.add(mesh);
  return node;
}

function scene(...nodes: Group[]): Group {
  const root = new Group();
  root.add(...nodes);
  return root;
}

const OK: GroundCheck = { checked: 616, floating: 0, buried: 0 };

describe('misplacedLayers con ground_check del pipeline', () => {
  it('no recomienda reasentar sólidos que solo intersectan una ladera', () => {
    const root = new Group();
    root.add(
      layer('T', 'terrain', 0),
      layer('E', 'building', 50, {
        checked: 2,
        floating: 0,
        buried: 0,
        intersecting: 1,
        partially_buried: 1,
      }),
    );
    expect(misplacedLayers(root)).toEqual([]);
  });
  it('avisa de las capas con sólidos que flotan o quedan enterrados', () => {
    const root = scene(
      layer('contours', 'terrain', -61),
      layer('-EDIFICIOS', 'building', 0, { checked: 616, floating: 53, buried: 0 }),
      layer('3_VEGETACIÓN', 'vegetation', -60, { checked: 1301, floating: 968, buried: 293 }),
      layer('-VIALES', 'road', -61, { checked: 40, floating: 0, buried: 0 }),
    );
    const found = misplacedLayers(root);
    expect(found.map((l) => l.layer)).toEqual(['-EDIFICIOS', '3_VEGETACIÓN']);
    const message = misplacedMessage(found);
    expect(message).toContain('-EDIFICIOS (53 flotan, 0 enterrados de 616)');
    expect(message).toContain('base_from_terrain: true');
  });

  it('modelo corregido: sin aviso, aunque la base esté en cota 0 (heurística no se usa)', () => {
    const root = scene(layer('contours', 'terrain', -61), layer('E', 'building', 0, OK));
    expect(misplacedLayers(root)).toEqual([]);
  });

  it('ignora ruido por debajo del 1 % de los sólidos', () => {
    const noise = { checked: 1301, floating: 3, buried: 0 };
    const root = scene(layer('contours', 'terrain', -61), layer('A', 'vegetation', -60, noise));
    expect(misplacedLayers(root)).toEqual([]);
  });

  it('teselas: la misma capa en varias teselas cuenta una vez', () => {
    const bad = { checked: 20, floating: 20, buried: 0 };
    const root = scene(
      scene(layer('T', 'terrain', -61), layer('EQ', 'building', 0, bad)),
      scene(layer('T', 'terrain', -40), layer('EQ', 'building', 0, bad)),
    );
    expect(misplacedLayers(root)).toEqual([{ layer: 'EQ', check: bad }]);
  });
});

describe('misplacedLayers en GLB antiguos (sin ground_check)', () => {
  it('terreno bajo cota 0 y edificios extruidos desde 0: probablemente flotan', () => {
    const root = scene(
      layer('contours', 'terrain', -61),
      layer('-EQUIPAMIENTOS', 'building', 0),
      layer('-EDIFICIOS', 'building', -55.3), // con cota propia: no se marca
    );
    expect(misplacedLayers(root)).toEqual([{ layer: '-EQUIPAMIENTOS', check: null }]);
  });

  it('terreno por encima de 0 y edificios en 0: quedan enterrados', () => {
    const root = scene(layer('CURVAS', 'terrain', 120), layer('EDIF', 'building', 0));
    expect(misplacedLayers(root).map((l) => l.layer)).toEqual(['EDIF']);
  });

  it('sin capa de terreno, o con terreno que pasa por cota 0, no hay aviso', () => {
    expect(misplacedLayers(scene(layer('EDIF', 'building', 0)))).toEqual([]);
    const flat = scene(layer('CURVAS', 'terrain', -0.5), layer('EDIF', 'building', 0));
    expect(misplacedLayers(flat)).toEqual([]);
  });
});
