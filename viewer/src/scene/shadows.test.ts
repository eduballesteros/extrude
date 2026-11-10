import { Box3, Matrix4, OrthographicCamera, Vector3 } from 'three';
import { describe, expect, it } from 'vitest';
import { fitShadow, shouldFitToView, viewBox } from './shadows';

const BOX = new Box3(new Vector3(-50, 0, -30), new Vector3(70, 25, 40));

function cornersInside(box: Box3, toSun: Vector3, mapSize?: number): boolean {
  const f = fitShadow(box, toSun, mapSize);
  const camera = new OrthographicCamera(f.left, f.right, f.top, f.bottom, f.near, f.far);
  camera.position.copy(f.position);
  camera.up.copy(f.up);
  camera.lookAt(f.target);
  camera.updateMatrixWorld();
  camera.updateProjectionMatrix();
  const toClip = new Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
  for (const x of [box.min.x, box.max.x])
    for (const y of [box.min.y, box.max.y])
      for (const z of [box.min.z, box.max.z]) {
        const p = new Vector3(x, y, z).applyMatrix4(toClip);
        if (Math.max(Math.abs(p.x), Math.abs(p.y), Math.abs(p.z)) > 1 + 1e-6) return false;
      }
  return true;
}

describe('fitShadow', () => {
  it.each([
    new Vector3(1, 1, 1),
    new Vector3(-0.3, 0.2, 0.9),
    new Vector3(0, 1, 0), // cénit: necesita otro vector "up"
    new Vector3(0.05, 0.05, -1),
  ])('el frustum de la luz contiene toda la caja (%o)', (dir) => {
    expect(cornersInside(BOX, dir.clone().normalize())).toBe(true);
    expect(cornersInside(BOX, dir.clone().normalize(), 2048)).toBe(true);
  });

  it('el redondeo a texels hace estable el frustum ante pequeños desplazamientos', () => {
    const dir = new Vector3(1, 2, 1).normalize();
    const a = fitShadow(BOX, dir, 1024);
    const moved = BOX.clone().translate(new Vector3(0.001, 0, 0));
    const b = fitShadow(moved, dir, 1024);
    const texel = (a.right - a.left) / 1024;
    expect(Math.abs(a.left - b.left)).toBeLessThanOrEqual(texel + 1e-9);
    expect((a.right - a.left) / texel).toBeCloseTo(Math.round((a.right - a.left) / texel), 6);
  });

  it('la luz se sitúa del lado del sol', () => {
    const dir = new Vector3(0, 1, 1).normalize();
    const f = fitShadow(BOX, dir);
    expect(f.position.clone().sub(f.target).normalize().dot(dir)).toBeCloseTo(1);
  });
});

describe('ajuste a la vista', () => {
  const city = new Box3(new Vector3(-3000, 0, -3000), new Vector3(3000, 60, 3000));

  it('recorta alrededor del objetivo y conserva la altura de la escena', () => {
    const box = viewBox(city, new Vector3(100, 0, 100), 200);
    expect(box.max.x - box.min.x).toBeCloseTo(2 * 200 * 1.6);
    expect(box.min.y).toBe(0);
    expect(box.max.y).toBe(60);
  });

  it('si el objetivo queda fuera, usa la escena entera', () => {
    expect(viewBox(city, new Vector3(1e6, 0, 0), 10).equals(city)).toBe(true);
  });

  it('auto: escena pequeña completa, ciudad solo la vista', () => {
    expect(shouldFitToView('auto', BOX)).toBe(false);
    expect(shouldFitToView('auto', city)).toBe(true);
    expect(shouldFitToView('scene', city)).toBe(false);
    expect(shouldFitToView('view', BOX)).toBe(true);
  });
});
