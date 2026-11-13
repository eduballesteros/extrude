import { Group, Object3D } from 'three';
import { describe, expect, it } from 'vitest';
import { effectiveHeight, formatEffective, HeightDrafts, parseHeight } from './heights';

function memory(): {
  getItem(k: string): string | null;
  setItem(k: string, v: string): void;
  removeItem(k: string): void;
  data: Map<string, string>;
} {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => void data.set(k, v),
    removeItem: (k) => void data.delete(k),
  };
}

describe('parseHeight', () => {
  it('acepta metros con punto, coma y unidad', () => {
    expect(parseHeight('12')).toEqual({ value: 12 });
    expect(parseHeight(' 12,5 ')).toEqual({ value: 12.5 });
    expect(parseHeight('7.25 m')).toEqual({ value: 7.25 });
    expect(parseHeight('3.14159')).toEqual({ value: 3.142 });
  });

  it.each(['', 'abc', '12 metros y medio', '0', '-4', '1001', '1e3', '12..5'])(
    'rechaza «%s» con un mensaje',
    (text) => {
      const parsed = parseHeight(text);
      expect('error' in parsed && parsed.error.length > 0).toBe(true);
    },
  );
});

describe('effectiveHeight', () => {
  it('lee las alturas reales de los objetos de la capa, también repartida en teselas', () => {
    const tile1 = new Object3D();
    tile1.userData = { layer: 'EDIF', objects: { height: [9, 12] } };
    const tile2 = new Group();
    const inner = new Object3D();
    inner.userData = { objects: { height: [21.5, null] } };
    tile2.add(inner);
    expect(effectiveHeight([tile1, tile2])).toEqual({ min: 9, max: 21.5, count: 3 });
    expect(formatEffective(effectiveHeight([tile1, tile2]))).toBe('9–21,5 m');
    expect(formatEffective(effectiveHeight([tile1]))).toBe('9–12 m');
  });

  it('una capa con una sola altura la muestra sin rango; sin alturas, raya', () => {
    const node = new Object3D();
    node.userData = { objects: { height: [21.5, 21.5] } };
    expect(formatEffective(effectiveHeight([node]))).toBe('21,5 m');
    expect(effectiveHeight([new Object3D()])).toBeNull();
    expect(formatEffective(null)).toBe('—');
  });
});

describe('HeightDrafts', () => {
  it('distingue pendiente de guardado y recupera el valor anterior', () => {
    const drafts = new HeightDrafts(memory());
    drafts.open('job-a');
    drafts.set('EDIF', '18', 12);
    expect(drafts.get('EDIF')).toBe('18');
    expect(drafts.pending).toEqual(['EDIF']);
    drafts.set('EDIF', '12', 12); // igual que lo guardado: deja de estar pendiente
    expect(drafts.pending).toEqual([]);
    drafts.set('EDIF', '20', 12);
    drafts.revert('EDIF');
    expect(drafts.get('EDIF')).toBeUndefined();
  });

  it('conserva lo pendiente al cerrar el panel o recargar, por proyecto', () => {
    const storage = memory();
    const first = new HeightDrafts(storage);
    first.open('job-a');
    first.set('EDIF', '18', null);
    first.set('VIALES', 'x', null);
    const reopened = new HeightDrafts(storage);
    reopened.open('job-a');
    expect(reopened.get('EDIF')).toBe('18');
    expect(reopened.error('VIALES')).toMatch(/número/);
    reopened.open('job-b');
    expect(reopened.pending).toEqual([]);
    reopened.open('job-a');
    reopened.clear();
    expect(storage.data.size).toBe(0);
  });

  it('prepara los cambios y no envía valores inválidos ni capas que ya no existen', () => {
    const drafts = new HeightDrafts(memory());
    drafts.open('job-a');
    drafts.set('EDIF', '18,5', null);
    drafts.remove('VIALES', 0.2);
    drafts.set('VIEJA', '9', null);
    expect(drafts.changes(new Set(['EDIF', 'VIALES']))).toEqual({ EDIF: 18.5, VIALES: null });
    drafts.set('EDIF', '-1', null);
    expect(() => drafts.changes(new Set(['EDIF']))).toThrow(/EDIF/);
  });

  it('quitar una altura que no estaba guardada no deja nada pendiente', () => {
    const drafts = new HeightDrafts(memory());
    drafts.open('job-a');
    drafts.set('EDIF', '18', null);
    drafts.remove('EDIF', null);
    expect(drafts.pending).toEqual([]);
  });
});
