import { describe, expect, it } from 'vitest';
import { openingSelection } from './generateDialog';

const views = ['vista-1', 'vista-2', 'vista-3'];

describe('vistas de origen al abrir «Generar con IA»', () => {
  it('desde una vista concreta solo se envía esa, aunque antes se eligieran otras', () => {
    expect(openingSelection(['vista-1'], views, 'vista-2', 'edit')).toEqual(['vista-2']);
    expect(openingSelection(['vista-1', 'vista-3'], views, 'vista-1', 'edit')).toEqual(['vista-1']);
  });

  it('desde el menú de la biblioteca se conserva la selección anterior', () => {
    expect(openingSelection(['vista-1', 'vista-3'], views, undefined, 'edit')).toEqual([
      'vista-1',
      'vista-3',
    ]);
  });

  it('con una generación en revisión o en curso no se cambia lo revisado', () => {
    expect(openingSelection(['vista-1'], views, 'vista-2', 'review')).toEqual(['vista-1']);
    expect(openingSelection(['vista-1'], views, 'vista-2', 'running')).toEqual(['vista-1']);
  });

  it('descarta vistas borradas y, si no queda ninguna, usa la primera', () => {
    expect(openingSelection(['borrada'], views, undefined, 'edit')).toEqual(['vista-1']);
    expect(openingSelection([], views, 'borrada', 'edit')).toEqual(['vista-1']);
    expect(openingSelection([], [], undefined, 'edit')).toEqual([]);
  });
});
