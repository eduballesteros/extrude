import {
  BoxGeometry,
  Group,
  InstancedMesh,
  Matrix4,
  Mesh,
  MeshBasicMaterial,
  PerspectiveCamera,
  PlaneGeometry,
  Raycaster,
  Vector2,
  Vector3,
} from 'three';
import { describe, expect, it } from 'vitest';
import { LayerHighlight } from './layerHighlight';
import { loupePosition, loupeSource, snapHit, toScreen } from './loupe';

const VIEWPORT = { width: 800, height: 600 };

function setup(): { camera: PerspectiveCamera; mesh: Mesh } {
  const camera = new PerspectiveCamera(50, VIEWPORT.width / VIEWPORT.height, 0.1, 1000);
  camera.position.set(0, 0, 20);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld(true);
  // Cuadrado de 10 m en el plano XY: vértices en (±5, ±5, 0)
  const mesh = new Mesh(new PlaneGeometry(10, 10), new MeshBasicMaterial());
  mesh.updateMatrixWorld(true);
  return { camera, mesh };
}

function snapAt(camera: PerspectiveCamera, mesh: Mesh, cursor: { x: number; y: number }) {
  const raycaster = new Raycaster();
  const ndc = new Vector2(
    (cursor.x / VIEWPORT.width) * 2 - 1,
    -(cursor.y / VIEWPORT.height) * 2 + 1,
  );
  raycaster.setFromCamera(ndc, camera);
  const hit = raycaster.intersectObject(mesh, true)[0];
  return hit ? snapHit(hit, raycaster.ray, camera, cursor, VIEWPORT) : null;
}

describe('snapHit', () => {
  it('se ajusta al vértice cuando el cursor está cerca de él', () => {
    const { camera, mesh } = setup();
    const corner = toScreen(new Vector3(5, 5, 0), camera, VIEWPORT);
    const snap = snapAt(camera, mesh, { x: corner.x - 6, y: corner.y + 6 });
    expect(snap?.kind).toBe('vertex');
    expect(snap?.point.distanceTo(new Vector3(5, 5, 0))).toBeLessThan(1e-9);
  });

  it('se ajusta al borde, sobre la arista, cuando no hay vértice cerca', () => {
    const { camera, mesh } = setup();
    const edge = toScreen(new Vector3(5, 1.5, 0), camera, VIEWPORT);
    const snap = snapAt(camera, mesh, { x: edge.x - 4, y: edge.y });
    expect(snap?.kind).toBe('edge');
    expect(snap?.point.x).toBeCloseTo(5, 6);
    expect(snap?.point.y).toBeCloseTo(1.5, 1);
  });

  it('lejos de vértices y bordes usa el punto de la superficie', () => {
    const { camera, mesh } = setup();
    const inside = toScreen(new Vector3(2, -3, 0), camera, VIEWPORT);
    const snap = snapAt(camera, mesh, inside);
    expect(snap?.kind).toBe('surface');
    expect(snap?.point.distanceTo(new Vector3(2, -3, 0))).toBeLessThan(1e-6);
  });

  it('en bloques instanciados usa la copia pulsada', () => {
    const { camera } = setup();
    const instanced = new InstancedMesh(new PlaneGeometry(2, 2), new MeshBasicMaterial(), 2);
    instanced.setMatrixAt(0, new Matrix4().makeTranslation(-4, 0, 0));
    instanced.setMatrixAt(1, new Matrix4().makeTranslation(4, 0, 0));
    instanced.instanceMatrix.needsUpdate = true;
    instanced.updateMatrixWorld(true);
    instanced.computeBoundingSphere();
    const corner = toScreen(new Vector3(5, 1, 0), camera, VIEWPORT);
    const snap = snapAt(camera, instanced, { x: corner.x - 5, y: corner.y + 5 });
    expect(snap?.kind).toBe('vertex');
    expect(snap?.point.distanceTo(new Vector3(5, 1, 0))).toBeLessThan(1e-9);
  });
});

describe('lupa', () => {
  it('amplía la zona bajo el cursor en píxeles reales del lienzo, con cualquier escala', () => {
    const cursor = { x: 400, y: 300 };
    // Pantalla normal: 1 px CSS = 1 px de lienzo
    expect(loupeSource(cursor, VIEWPORT, { width: 800, height: 600 }, 152, 4)).toEqual({
      x: 381,
      y: 281,
      width: 38,
      height: 38,
    });
    // Pantalla de alta densidad o zoom del navegador al 200 %
    expect(loupeSource(cursor, VIEWPORT, { width: 1600, height: 1200 }, 152, 4)).toEqual({
      x: 762,
      y: 562,
      width: 76,
      height: 76,
    });
  });

  it('se coloca junto al cursor sin salirse del visor', () => {
    expect(loupePosition({ x: 100, y: 100 }, VIEWPORT, 152)).toEqual({ x: 122, y: 122 });
    const corner = loupePosition({ x: 790, y: 590 }, VIEWPORT, 152);
    expect(corner.x + 152).toBeLessThanOrEqual(790);
    expect(corner.y + 152).toBeLessThanOrEqual(590);
    const tiny = loupePosition({ x: 50, y: 50 }, { width: 120, height: 120 }, 152);
    expect(tiny).toEqual({ x: 0, y: 0 });
  });
});

describe('LayerHighlight', () => {
  it('copia mallas, instancias y nodos de todas las teselas sin tocar el modelo', () => {
    const tileA = new Group();
    const mesh = new Mesh(new BoxGeometry(1, 1, 1), new MeshBasicMaterial({ color: 'red' }));
    mesh.position.set(10, 0, 0);
    tileA.add(mesh);
    const tileB = new Group();
    const instanced = new InstancedMesh(new BoxGeometry(1, 1, 1), new MeshBasicMaterial(), 3);
    tileB.add(instanced);
    const material = mesh.material;

    const highlight = new LayerHighlight();
    expect(highlight.show([tileA, tileB])).toBe(2);
    const [copy, copies] = highlight.group.children as [Mesh, InstancedMesh];
    expect(copy.geometry).toBe(mesh.geometry); // misma geometría, sin duplicar memoria
    expect(copy.matrixWorld.elements[12]).toBe(10); // misma posición en el mundo
    expect(copies.count).toBe(3);
    expect(copies.instanceMatrix).toBe(instanced.instanceMatrix);
    expect(mesh.material).toBe(material); // el modelo no cambia

    // Las copias no se pueden pulsar ni medir
    const raycaster = new Raycaster(new Vector3(10, 0, 5), new Vector3(0, 0, -1));
    expect(raycaster.intersectObject(highlight.group, true)).toHaveLength(0);

    highlight.clear();
    expect(highlight.count).toBe(0);
  });
});
