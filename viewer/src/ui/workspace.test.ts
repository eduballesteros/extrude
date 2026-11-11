import { PerspectiveCamera, Vector3 } from 'three';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PRESET_DIRECTIONS, type ViewPreset } from '../scene/navigation';
import { download } from '../scene/loader';
import { sunTimes } from '../scene/sun';
import { northScreenAngle, projectCube } from '../scene/viewCube';
import { compactNumber, parseNumeric } from './dom';
import { primaryRows } from './info';
import { dialPoint, dialToSun, formatHour, fromEv, shadowMode, toEv } from './inspector';
import { originBadge } from './topbar';
import { formatOf } from './viewsPanel';

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

describe('controles del inspector', () => {
  it('modo de sombras: apagadas / PCF / VSM', () => {
    expect(shadowMode(false, 'vsm')).toBe('off');
    expect(shadowMode(true, 'pcf')).toBe('pcf');
    expect(shadowMode(true, 'vsm')).toBe('vsm');
  });

  it('exposición en EV ida y vuelta', () => {
    expect(toEv(1)).toBe(0);
    expect(toEv(2)).toBeCloseTo(1);
    expect(fromEv(-1)).toBe(0.5);
    expect(fromEv(toEv(1.7))).toBeCloseTo(1.7);
  });

  it('dial solar: norte arriba, cénit en el centro, inversa coherente', () => {
    const [x0, y0] = dialPoint(0, 0, 40);
    expect(x0).toBeCloseTo(0);
    expect(y0).toBeCloseTo(-40);
    const [x1, y1] = dialPoint(90, 90, 40);
    expect(Math.hypot(x1, y1)).toBeCloseTo(0);
    const [x2, y2] = dialPoint(214.3, 41.2, 40);
    const back = dialToSun(x2, y2, 40);
    expect(back.azimuthDeg).toBeCloseTo(214.3, 3);
    expect(back.elevationDeg).toBeCloseTo(41.2, 3);
  });

  it('campo numérico: coma decimal, límites, paso y rechazo de basura', () => {
    expect(parseNumeric('1,5', 0, 10, 0.1)).toBe(1.5);
    expect(parseNumeric(' 42 ', 0, 10, 1)).toBe(10);
    expect(parseNumeric('-3', 0, 10, 1)).toBe(0);
    expect(parseNumeric('0.123', 0, 1, 0.05)).toBe(0.1);
    expect(parseNumeric('abc', 0, 10, 1)).toBeNull();
    expect(parseNumeric('', 0, 10, 1)).toBeNull();
    expect(parseNumeric('1e400', 0, 10, 1)).toBeNull();
  });

  it('números compactos para la barra de estado', () => {
    expect(compactNumber(1_400_000)).toBe('1.4M');
    expect(compactNumber(12_300)).toBe('12.3K');
    expect(compactNumber(950)).toBe('950');
  });
});

describe('barra superior, vistas e inspección', () => {
  it('distintivo de origen: solo el CRS (el desplazamiento va en el título emergente)', () => {
    expect(originBadge(null)).toBeNull();
    expect(originBadge({ origin_offset: [0, 0, 0], crs: null } as never)).toBeNull();
    expect(
      originBadge({ origin_offset: [440123.4, 4474567.8, 0], crs: 'EPSG:25830' } as never),
    ).toBe('EPSG:25830');
    expect(originBadge({ origin_offset: [10, 20, 0], crs: null } as never)).toBeNull();
  });

  it('formato de render rápido o personalizado', () => {
    expect(formatOf(3840, 2160)).toBe('16:9');
    expect(formatOf(4961, 3508)).toBe('A3');
    expect(formatOf(1000, 700)).toBe('');
  });

  it('tarjeta de inspección: capa, altura, plantas y handle formateados', () => {
    const rows = primaryRows({
      point: new Vector3(),
      layer: 'EDIFICIOS',
      kind: 'building',
      fields: [
        ['Objeto', '3'],
        ['Altura (m)', '18'],
        ['Plantas', '6'],
        ['Cota base (m)', '—'],
        ['Handle', '3fa1'],
      ],
    });
    expect(rows).toEqual([
      ['Capa', 'EDIFICIOS'],
      ['Altura', '18.0 m'],
      ['Plantas', '6'],
      ['Handle', '#3FA1'],
    ]);
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
