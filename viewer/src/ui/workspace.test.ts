import { PerspectiveCamera, Vector3 } from 'three';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PRESET_DIRECTIONS, type ViewPreset } from '../scene/navigation';
import { download } from '../scene/loader';
import { sunTimes } from '../scene/sun';
import { northScreenAngle, projectCube } from '../scene/viewCube';
import { formatHour, shadowQuality } from './inspector';

function cameraFrom(preset: ViewPreset): PerspectiveCamera {
  const camera = new PerspectiveCamera();
  camera.position.copy(new Vector3(...PRESET_DIRECTIONS[preset]).normalize().multiplyScalar(10));
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld();
  return camera;
}

describe('cubo de orientación', () => {
  it('en planta solo se ve la cara superior, centrada', () => {
    const faces = projectCube(cameraFrom('top').quaternion, 34);
    expect(faces.map((f) => f.id)).toEqual(['top']);
    expect(Math.abs(faces[0]!.center[0])).toBeLessThan(0.1);
    expect(Math.abs(faces[0]!.center[1])).toBeLessThan(0.1);
  });

  it('desde el sur se ve de frente la cara S y la planta queda arriba en pantalla', () => {
    const faces = projectCube(cameraFrom('south').quaternion, 34);
    expect(faces.map((f) => f.id)).toEqual(['south']);
    const iso = projectCube(cameraFrom('iso').quaternion, 34);
    expect(iso.map((f) => f.id).sort()).toEqual(['east', 'south', 'top']);
    const top = iso.find((f) => f.id === 'top')!;
    expect(top.center[1]).toBeLessThan(0); // arriba (y de pantalla negativa)
    expect(iso[iso.length - 1]!.depth).toBeGreaterThanOrEqual(iso[0]!.depth);
  });

  it('cada cara proyecta 4 vértices dentro del tamaño indicado', () => {
    for (const face of projectCube(cameraFrom('iso').quaternion, 34)) {
      expect(face.points).toHaveLength(4);
      for (const [x, y] of face.points) expect(Math.hypot(x, y)).toBeLessThanOrEqual(34);
    }
  });

  it('la aguja del norte apunta arriba en planta y gira con el norte real', () => {
    const q = cameraFrom('top').quaternion;
    expect(northScreenAngle(q, 0)).toBeCloseTo(0, 1);
    expect(northScreenAngle(q, 90)).toBeCloseTo(90, 1); // norte real al este del dibujo
    expect(Math.abs(northScreenAngle(cameraFrom('north').quaternion, 0))).toBeCloseTo(180, 0);
  });
});

describe('línea de tiempo solar', () => {
  it('Madrid, solsticio de verano: amanecer ~6:45 y ocaso ~21:48 (UTC+2)', () => {
    const t = sunTimes('2024-06-21', 40.4168, -3.7038, 2);
    expect(t.sunrise!).toBeGreaterThan(6.6);
    expect(t.sunrise!).toBeLessThan(6.9);
    expect(t.sunset!).toBeGreaterThan(21.7);
    expect(t.sunset!).toBeLessThan(21.9);
    expect(t.noon).toBeCloseTo(14.27, 1);
  });

  it('día polar: sin amanecer ni ocaso', () => {
    const t = sunTimes('2024-06-21', 78.2, 15.6, 2); // Svalbard
    expect(t.sunrise).toBeNull();
    expect(t.sunset).toBeNull();
  });

  it('formato de hora', () => {
    expect(formatHour(6.75)).toBe('06:45');
    expect(formatHour(23.999)).toBe('00:00');
    expect(formatHour(14.27)).toBe('14:16');
  });
});

describe('calidad de sombras', () => {
  it('se deduce de activado + resolución', () => {
    expect(shadowQuality(false, 4096)).toBe('off');
    expect(shadowQuality(true, 2048)).toBe('medium');
    expect(shadowQuality(true, 4096)).toBe('high');
    expect(shadowQuality(true, 8192)).toBe('high');
  });
});

describe('progreso de descarga', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('informa de fracciones crecientes hasta 1 con Content-Length', async () => {
    const chunks = [new Uint8Array(40), new Uint8Array(60)];
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const c of chunks) controller.enqueue(c);
        controller.close();
      },
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(body, { headers: { 'Content-Length': '100' } })),
    );
    const seen: (number | null)[] = [];
    const bytes = await download('/m.glb', (_, f) => seen.push(f));
    expect(bytes.byteLength).toBe(100);
    expect(seen).toEqual([0, 0.4, 1]);
  });

  it('sin Content-Length la fase es indeterminada', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new Uint8Array(10))));
    const seen: (number | null)[] = [];
    await download('/m.glb', (_, f) => seen.push(f));
    expect(seen.every((f) => f === null)).toBe(true);
  });

  it('error HTTP con mensaje claro', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 404 })));
    await expect(download('/no.glb')).rejects.toThrow('HTTP 404');
  });
});
