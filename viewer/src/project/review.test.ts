import { describe, expect, it } from 'vitest';
import { summarizeQuality } from '../convert/report';
import {
  type ProjectReview,
  reviewIssues,
  revisionMismatch,
  serverNotice,
  type SupportLayer,
} from './review';

function support(overrides: Partial<SupportLayer> = {}): SupportLayer {
  return {
    layer: 'EDIF',
    kind: 'building',
    baseMode: 'auto',
    objects: 10,
    checked: 10,
    floating: 0,
    buried: 0,
    partialGap: 0,
    intersecting: 0,
    partiallyBuried: 0,
    extrapolated: 0,
    foundations: 0,
    highFoundations: 0,
    maxFoundationM: 0,
    provisionalHeights: 0,
    canRepair: false,
    recommended: false,
    note: '',
    ...overrides,
  };
}

function review(overrides: Partial<ProjectReview> = {}): ProjectReview {
  return {
    job: 'a'.repeat(32),
    revision: 'sha256:abc',
    repeatable: true,
    terrain: { status: 'valid', reason: null },
    summary: null,
    support: [support()],
    pending: [],
    outdated: false,
    adjustments: {},
    canRestore: false,
    configGenerated: true,
    build: { id: 'x', source: 'x', stale: false, started: '2026-11-13T10:00:00', pid: 1 },
    ...overrides,
  };
}

describe('revisionMismatch', () => {
  it('acepta el modelo solo si es de la revisión del informe', () => {
    expect(revisionMismatch('sha256:aaa', 'sha256:aaa')).toBeNull();
    expect(revisionMismatch('sha256:aaaaaaaaaaaa', 'sha256:bbbbbbbbbbbb')).toMatch(
      /bbbbbbbbbb.*aaaaaaaaaa.*no corresponden/,
    );
  });

  it('sin dato de revisión (servidor o GLB anteriores) no inventa un fallo', () => {
    expect(revisionMismatch(undefined, 'sha256:aaa')).toBeNull();
    expect(revisionMismatch('sha256:aaa', null)).toBeNull();
  });
});

describe('serverNotice', () => {
  it('servidor al día: sin aviso', () => {
    expect(serverNotice(review().build)).toBeNull();
  });

  it('avisa si el proceso arrancó antes de cambiar el código', () => {
    const notice = serverNotice({ ...review().build, stale: true });
    expect(notice).toMatch(/2026-11-13 a las 10:00:00/);
    expect(notice).toMatch(/Reinícialo/);
  });

  it('un servidor que no informa de su versión es anterior al visor', () => {
    expect(serverNotice(undefined)).toMatch(/anterior a este visor/);
  });
});

describe('reviewIssues', () => {
  it('proyecto sano: nada que revisar', () => {
    expect(reviewIssues(review())).toEqual([]);
  });

  it('distingue apoyo, capas pendientes, alturas provisionales y zócalos altos', () => {
    const issues = reviewIssues(
      review({
        support: [
          support({ floating: 21, baseMode: 'fixed', canRepair: true, recommended: true }),
          support({ layer: 'B', partialGap: 2, provisionalHeights: 5, highFoundations: 7 }),
        ],
        pending: [
          {
            name: 'SECTOR',
            summary: '3 contornos cerrados',
            suggested_kind: null,
            evidence: { closed_outlines: 3, hatches: 1, meshes: 0 },
          },
        ],
      }),
    );
    expect(issues.map((i) => i.kind)).toEqual(['support', 'pending', 'provisional', 'foundation']);
    expect(issues[0]?.text).toBe('Apoyo en el terreno: 21 flotan, 2 con contacto parcial.');
    expect(issues[1]?.text).toMatch(/1 capa no convertida tiene geometría \(4 elementos\)/);
    expect(issues[2]?.text).toMatch(/5 edificios tienen una altura provisional/);
  });

  it('cero flotantes no oculta que haya capas pendientes ni un terreno inválido', () => {
    const issues = reviewIssues(
      review({
        terrain: { status: 'invalid', reason: 'todas sus cotas son 0' },
        pending: [
          {
            name: 'A',
            summary: '',
            suggested_kind: 'building',
            evidence: { closed_outlines: 577, hatches: 0, meshes: 0 },
          },
        ],
        outdated: true,
      }),
    );
    expect(issues.map((i) => i.kind)).toEqual(['terrain', 'pending', 'outdated']);
  });
});

describe('summarizeQuality con el resumen del servidor', () => {
  it('separa lo convertido de lo ignorado y lo pendiente', () => {
    const quality = summarizeQuality({
      warnings: [],
      output: { triangles: 0, total_size_bytes: 0 },
      timings_s: {},
      layers: {
        EDIF: {
          heights: { default_height: 21 },
          ground_check: { checked: 21, floating: 0, buried: 0, partial_gap: 2, unverified: 1 },
          high_foundations: 7,
        },
      },
      summary: {
        converted_objects: 22,
        ignored_layers: 11,
        pending_layers: 9,
        pending_elements: 21,
        provisional_heights: 21,
      },
      terrain: { status: 'valid', reason: null },
      pending_layers: [{ name: 'EQ', summary: '1 contornos cerrados' }],
    });
    expect(quality.floating).toBe(0);
    expect(quality.partialGap).toBe(2);
    expect(quality.unverified).toBe(1);
    expect(quality.highFoundations).toBe(7);
    expect(quality.ignoredLayers).toBe(11);
    expect(quality.pendingLayers).toBe(9);
    expect(quality.pendingNames).toEqual(['EQ']);
    expect(quality.defaultHeights).toBe(21);
  });

  it('informes anteriores: no se inventan recuentos que no traen', () => {
    const quality = summarizeQuality({
      warnings: [],
      output: { triangles: 0, total_size_bytes: 0 },
      timings_s: {},
      layers: {},
    });
    expect(quality.summaryAvailable).toBe(false);
    expect(quality.pendingLayers).toBe(0);
    expect(quality.terrainStatus).toBeNull();
  });
});
