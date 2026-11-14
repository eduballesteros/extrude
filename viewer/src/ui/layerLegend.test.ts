import { describe, expect, it } from 'vitest';
import { FIDELITY_SENTENCE, finalPrompt } from './generateDialog';
import { layerLegend } from './layerLegend';

const layer = (name: string, kind: string, visible = true) => ({ name, kind, visible });

describe('leyenda de capas para la IA', () => {
  it('describe cada tipo visible, para cualquier proyecto y nombre de capa', () => {
    const legend = layerLegend([
      layer('_UNIFAMILIARES', 'building'),
      layer('RED_VIARIA', 'road'),
      layer('_LAGUNA', 'water'),
      layer('ZONAS_VERDES', 'vegetation'),
      layer('curvas', 'terrain'),
    ])!;
    expect(legend).toMatch(/^Leyenda de la imagen de referencia: los volúmenes son edificios/);
    expect(legend).toContain('(capas UNIFAMILIARES)');
    expect(legend).toContain('son viales');
    expect(legend).toContain('no muros ni edificios (capas RED VIARIA)');
    expect(legend).toContain('un estanque (capas LAGUNA)');
    expect(legend).toContain('parques y zonas verdes');
    expect(legend).toContain('terreno natural');
    expect(legend.endsWith('.')).toBe(true);
  });

  it('omite capas ocultas, de tipo desconocido o ignoradas', () => {
    const legend = layerLegend([
      layer('EDIFICIOS', 'building'),
      layer('VIALES', 'road', false),
      layer('MOBILIARIO', 'unknown'),
      layer('TEXTOS', 'ignore'),
    ])!;
    expect(legend).toContain('edificios');
    expect(legend).not.toContain('viales');
    expect(legend).not.toMatch(/MOBILIARIO|TEXTOS/);
    expect(layerLegend([layer('VIALES', 'road', false)])).toBeNull();
    expect(layerLegend([])).toBeNull();
  });

  it('cita como mucho cuatro capas por tipo', () => {
    const many = ['A', 'B', 'C', 'D', 'E', 'F'].map((n) => layer(`EDIF_${n}`, 'building'));
    expect(layerLegend(many)).toContain('(capas EDIF A, EDIF B, EDIF C, EDIF D y 2 más)');
  });
});

describe('prompt que se envía', () => {
  it('añade la leyenda entre el texto y la frase de fidelidad', () => {
    expect(finalPrompt('Render', true, 'Leyenda: x.')).toBe(
      `Render\n\nLeyenda: x.\n\n${FIDELITY_SENTENCE}`,
    );
    expect(finalPrompt('Render', false, 'Leyenda: x.')).toBe('Render\n\nLeyenda: x.');
    expect(finalPrompt('Render', true)).toBe(`Render\n\n${FIDELITY_SENTENCE}`);
  });

  it('sin texto del usuario no se añade nada', () => {
    expect(finalPrompt('  ', true, 'Leyenda: x.')).toBe('');
  });
});
