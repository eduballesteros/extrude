import { OrthographicCamera, PerspectiveCamera, Vector3 } from 'three';
import { describe, expect, it, vi } from 'vitest';
import {
  formatMeters,
  MeasureTool,
  measureBetween,
  measurementRows,
  worldPerPixel,
} from './measure';

/** Punto de escena (Y-up) a partir de coordenadas del dibujo: X, Y en planta y Z cota. */
const drawing = (x: number, y: number, z: number): Vector3 => new Vector3(x, z, -y);

describe('measureBetween (escena Y-up = dibujo Z-up)', () => {
  it('triángulo 3-4-5 en planta sin desnivel', () => {
    const m = measureBetween(drawing(0, 0, 10), drawing(3, 4, 10));
    expect(m.distance).toBeCloseTo(5, 12);
    expect(m.horizontal).toBeCloseTo(5, 12);
    expect(m.vertical).toBe(0);
    expect(m.slopePct).toBe(0);
  });

  it('distancia 3D, planta y desnivel con signo (B − A)', () => {
    const a = drawing(100, 200, 50);
    const b = drawing(106, 208, 74); // planta 10 m, sube 24 m → 3D 26 m
    const up = measureBetween(a, b);
    expect(up.horizontal).toBeCloseTo(10, 12);
    expect(up.vertical).toBeCloseTo(24, 12);
    expect(up.distance).toBeCloseTo(26, 12);
    expect(up.slopePct).toBeCloseTo(240, 9);
    const down = measureBetween(b, a);
    expect(down.vertical).toBeCloseTo(-24, 12);
    expect(down.distance).toBeCloseTo(26, 12);
    expect(down.slopePct).toBeCloseTo(240, 9); // la pendiente no tiene signo
  });

  it('altura de una fachada: misma vertical, sin pendiente', () => {
    const m = measureBetween(drawing(5, 5, -61.2), drawing(5, 5, -49.2));
    expect(m.horizontal).toBe(0);
    expect(m.vertical).toBeCloseTo(12, 12);
    expect(m.distance).toBeCloseTo(12, 12);
    expect(m.slopePct).toBeNull();
  });

  it('coordenadas grandes (UTM sin recentrar) conservan precisión en float64', () => {
    const m = measureBetween(drawing(440291.25, 4474254.5, 650), drawing(440291.25, 4474256, 650));
    expect(m.horizontal).toBeCloseTo(1.5, 9);
  });
});

describe('formato de la etiqueta', () => {
  it('metros con dos decimales y desnivel con signo', () => {
    expect(formatMeters(12.3456)).toBe('12.35 m');
    expect(formatMeters(7.2, true)).toBe('+7.20 m');
    expect(formatMeters(-7.2, true)).toBe('−7.20 m');
    expect(formatMeters(0.001, true)).toBe('0.00 m');
  });

  it('filas 3D, Planta, Δz y Pendiente (sin pendiente en vertical)', () => {
    const rows = measurementRows(measureBetween(drawing(0, 0, 0), drawing(6, 8, -5)));
    expect(rows).toEqual([
      ['3D', '11.18 m'],
      ['Planta', '10.00 m'],
      ['Δz', '−5.00 m'],
      ['Pendiente', '50.0 %'],
    ]);
    const vertical = measurementRows(measureBetween(drawing(0, 0, 0), drawing(0, 0, 3)));
    expect(vertical.map(([k]) => k)).toEqual(['3D', 'Planta', 'Δz']);
  });
});

describe('worldPerPixel (marcadores de tamaño constante en pantalla)', () => {
  it('perspectiva: crece con la distancia; ortográfica: depende solo del encuadre', () => {
    const persp = new PerspectiveCamera(90, 1, 0.1, 1000);
    persp.position.set(0, 0, 10);
    const near = worldPerPixel(persp, new Vector3(0, 0, 0), 1000);
    const far = worldPerPixel(persp, new Vector3(0, 0, -10), 1000);
    expect(near).toBeCloseTo(0.02, 9); // 2·10·tan(45°)/1000
    expect(far).toBeCloseTo(2 * near, 9);
    const ortho = new OrthographicCamera(-50, 50, 50, -50, 0.1, 1000);
    ortho.zoom = 2;
    expect(worldPerPixel(ortho, new Vector3(0, 0, -500), 500)).toBeCloseTo(0.1, 9);
  });
});

describe('MeasureTool', () => {
  it('clic A, clic B → medición; un tercer clic empieza otra', () => {
    const onChange = vi.fn();
    const tool = new MeasureTool(null, onChange);
    tool.setActive(true);
    expect(tool.addPoint(drawing(0, 0, 0))).toBeNull();
    expect(tool.pointCount).toBe(1);
    const m = tool.addPoint(drawing(3, 4, 12))!;
    expect(m.distance).toBeCloseTo(13, 12);
    expect(tool.measurement?.vertical).toBeCloseTo(12, 12);
    expect(tool.addPoint(drawing(10, 10, 0))).toBeNull();
    expect(tool.pointCount).toBe(1);
    expect(onChange).toHaveBeenCalled();
  });

  it('el doble clic (mismo punto) no crea una medición de 0 m', () => {
    const tool = new MeasureTool(null);
    tool.addPoint(drawing(1, 1, 1));
    tool.addPoint(drawing(1, 1, 1));
    expect(tool.pointCount).toBe(1);
    expect(tool.measurement).toBeNull();
  });

  it('dibuja línea, catetos (planta + vertical) y marcadores; clear los oculta', () => {
    const tool = new MeasureTool(null);
    tool.addPoint(drawing(0, 0, 0));
    tool.addPoint(drawing(10, 0, 5));
    const [legs, line, markerA, markerB] = tool.group.children;
    expect(line!.visible && legs!.visible && markerA!.visible && markerB!.visible).toBe(true);
    const corner = new Vector3().fromBufferAttribute(
      (legs as unknown as { geometry: { attributes: { position: never } } }).geometry.attributes
        .position,
      1,
    );
    // esquina del triángulo: bajo B, a la cota de A
    expect(corner.distanceTo(new Vector3(10, 0, 0))).toBeLessThan(1e-12);
    tool.clear();
    expect(tool.pointCount).toBe(0);
    expect(tool.group.children.some((c) => c.visible)).toBe(false);
  });

  it('desactivar la herramienta borra la medición', () => {
    const onChange = vi.fn();
    const tool = new MeasureTool(null, onChange);
    tool.setActive(true);
    tool.addPoint(drawing(0, 0, 0));
    tool.setActive(false);
    expect(tool.active).toBe(false);
    expect(tool.pointCount).toBe(0);
    expect(onChange).toHaveBeenCalledTimes(3); // activar, punto, desactivar
  });
});
