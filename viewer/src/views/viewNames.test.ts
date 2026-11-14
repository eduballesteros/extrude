import { describe, expect, it } from 'vitest';
import { nextViewName } from './viewManager';

const named = (...names: string[]) => names.map((name) => ({ name }));

describe('nombre de una vista nueva', () => {
  it('empieza en Vista 1 y sigue numerando', () => {
    expect(nextViewName([])).toBe('Vista 1');
    expect(nextViewName(named('Vista 1', 'Vista 2'))).toBe('Vista 3');
  });

  it('no repite un nombre tras borrar una vista anterior', () => {
    expect(nextViewName(named('Vista 2'))).toBe('Vista 3');
    expect(nextViewName(named('Vista 1', 'Vista 3'))).toBe('Vista 4');
  });

  it('ignora los nombres puestos a mano', () => {
    expect(nextViewName(named('Aérea laguna', 'Vista 1'))).toBe('Vista 2');
    expect(nextViewName(named('Aérea laguna'))).toBe('Vista 1');
  });
});
