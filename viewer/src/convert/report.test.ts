import { describe, expect, it } from 'vitest';
import { type ConversionReport, summarizeQuality } from './report';

const base: ConversionReport = {
  warnings: [],
  output: { triangles: 1, total_size_bytes: 1 },
  timings_s: {},
};

describe('resumen de calidad', () => {
  it('agrega extrapolaciones y distingue informes sin diagnóstico de cobertura', () => {
    const quality = summarizeQuality({
      ...base,
      layers: {
        A: { terrain_coverage: { checked: 3, covered: 2, extrapolated: 1, unknown: 0 } },
        B: { terrain_coverage: { checked: 2, covered: 0, extrapolated: 2, unknown: 0 } },
      },
    });
    expect(quality.coverageAvailable).toBe(true);
    expect(quality.extrapolated).toBe(3);
    expect(summarizeQuality(base).coverageAvailable).toBe(false);
  });
  it('muestra los zócalos como adaptaciones y conserva los recuentos de apoyo', () => {
    const quality = summarizeQuality({
      ...base,
      layers: {
        A: {
          terrain_foundations: 5,
          max_foundation_height_m: 26.77,
          ground_check: { checked: 5, floating: 0, buried: 0, partially_buried: 0 },
        },
        B: { terrain_foundations: 2, max_foundation_height_m: 3 },
      },
    });
    expect(quality).toMatchObject({
      foundations: 7,
      maxFoundationHeight: 26.77,
      floating: 0,
      buried: 0,
      partiallyBuried: 0,
    });
    expect(summarizeQuality(base).foundations).toBe(0);
  });
  it('suma capas y separa alturas por defecto de alturas configuradas', () => {
    const quality = summarizeQuality({
      ...base,
      layers: {
        A: {
          heights: { default_height: 3, height: 10, floors: 5 },
          ambiguous_heights: 2,
          dropped_without_height: 1,
          nearby_text_heights: 4,
        },
        B: { heights: { default_height: 1 }, ambiguous_heights: 1, dropped_without_height: 2 },
      },
    });
    expect(quality).toMatchObject({
      available: true,
      defaultHeights: 4,
      ambiguous: 3,
      dropped: 3,
      nearby: 4,
      groundAvailable: false,
    });
  });

  it('separa flotación completa e intersección con ladera y no repite agregados por tesela', () => {
    const quality = summarizeQuality({
      ...base,
      layers: {
        E: {
          ground_check: {
            checked: 10,
            floating: 1,
            buried: 2,
            intersecting: 3,
            partially_buried: 4,
          },
        },
        T: {},
      },
    });
    expect(quality).toMatchObject({
      groundAvailable: true,
      checked: 10,
      floating: 1,
      buried: 2,
      intersecting: 3,
      partiallyBuried: 4,
    });
  });

  it('distingue informes antiguos y la ausencia de comprobación de terreno', () => {
    expect(summarizeQuality(base)).toMatchObject({ available: false, groundAvailable: false });
    expect(summarizeQuality({ ...base, layers: {} })).toMatchObject({
      available: true,
      groundAvailable: false,
    });
  });
});
