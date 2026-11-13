import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  AiClient,
  AiRequestError,
  applicable,
  defaultSelection,
  planIsCurrent,
  requestKey,
  RequestGate,
  toggleOperation,
  type EditPlan,
  type PlanOperation,
  type ProposalRequest,
} from './ai';
import { describeOperation } from '../ui/editSection';
import { resolveSunIso } from './viewerOps';
import type { SunState } from '../state';

function operation(overrides: Partial<PlanOperation>): PlanOperation {
  return {
    id: 'op1',
    tool: 'object.move',
    toolVersion: '1',
    title: 'Mover',
    executor: 'server',
    args: { dxM: 5, dyM: 0 },
    objectIds: ['obj-1'],
    pending: null,
    dependsOn: [],
    basis: 'explicit',
    reason: 'Pedido.',
    status: 'ready',
    messages: [],
    incompatible: [],
    keptManual: [],
    impact: { objects: 1, labels: ['E-001'] },
    ...overrides,
  };
}

function plan(operations: PlanOperation[], baseRevision = 'rev-001'): EditPlan {
  return {
    schemaVersion: 2,
    planId: 'plan-0123456789abcdef',
    baseRevision,
    geometryRevision: 'sha256:x',
    materialRevision: null,
    catalogVersion: 'library-1',
    provider: 'simulated',
    model: 'reglas',
    simulated: true,
    promptVersion: 'edit-prompt-1',
    createdAt: null,
    scope: { kind: 'all', objectsSent: 3, objectsInModel: 3 },
    summary: 'Cambios.',
    explicitRequirements: [],
    assumptions: [],
    operations,
    pending: [],
    conflicts: [],
    unsupported: [],
    validation: { ok: true },
    cached: false,
    usage: null,
    durationMs: 0,
    costEstimate: null,
    session: { requests: 1, limit: 5 },
  };
}

const request: ProposalRequest = {
  provider: 'anthropic',
  description: 'Sube E-001 dos plantas',
  scope: 'all',
  selection: [],
  layers: [],
  baseRevision: 'rev-001',
  consent: true,
  continueAfterLimit: false,
};

afterEach(() => vi.unstubAllGlobals());

describe('selección de operaciones', () => {
  const chain = plan([
    operation({ id: 'op1' }),
    operation({ id: 'op2', dependsOn: ['op1'] }),
    operation({ id: 'op3', dependsOn: ['op2'] }),
    operation({ id: 'op4', basis: 'assumption' }),
    operation({ id: 'op5', status: 'blocked' }),
  ]);

  it('marca por defecto lo explícito y listo, nunca supuestos ni bloqueadas', () => {
    expect([...defaultSelection(chain)].sort()).toEqual(['op1', 'op2', 'op3']);
  });

  it('marcar arrastra las dependencias y desmarcar arrastra a las dependientes', () => {
    expect([...toggleOperation(chain, new Set(), 'op3', true)].sort()).toEqual([
      'op1',
      'op2',
      'op3',
    ]);
    expect([...toggleOperation(chain, new Set(['op1', 'op2', 'op3']), 'op1', false)]).toEqual([]);
  });

  it('una duda se aplica solo si el usuario la resolvió, y un dato solo si lo dio', () => {
    const pending = operation({ status: 'needs_resolution', pending: 'q1' });
    expect(applicable(pending, {}, {})).toBe(false);
    expect(applicable(pending, { q1: ['obj-2'] }, {})).toBe(true);
    const input = operation({ id: 'op9', status: 'needs_input' });
    expect(applicable(input, {}, {})).toBe(false);
    expect(applicable(input, {}, { op9: { floorHeightM: 3.2 } })).toBe(true);
    expect(applicable(operation({ status: 'incompatible' }), {}, {})).toBe(false);
  });

  it('un dato pedido por la operación (cota de apoyo) se exige con su clave', () => {
    const site = operation({
      id: 'op2',
      tool: 'site.place',
      status: 'needs_input',
      args: { assetId: 'tree_round', layout: 'row', count: 10, spacingM: 5, baseZM: null },
      inputsNeeded: [{ key: 'baseZM', label: 'Cota de apoyo', unit: 'm', min: -1000, max: 9000 }],
    });
    expect(applicable(site, {}, { op2: { floorHeightM: 3 } })).toBe(false);
    expect(applicable(site, {}, { op2: { baseZM: 0 } })).toBe(true);
    expect(describeOperation(site, (id) => id)).toBe('10 × árbol de copa en fila cada 5 m');
    const unique = operation({ tool: 'block.make_unique', args: { confirm: true } });
    expect(describeOperation(unique, (id) => id)).toContain('las demás no cambian');
  });

  it('un plan de otra revisión está obsoleto', () => {
    expect(planIsCurrent(plan([], 'rev-001'), 'rev-001')).toBe(true);
    expect(planIsCurrent(plan([], 'rev-001'), 'rev-002')).toBe(false);
    expect(planIsCurrent(plan([], 'rev-001'), null)).toBe(false);
  });
});

describe('autorización ligada al contenido', () => {
  it('cambiar proveedor, texto, alcance, selección o revisión invalida lo revisado', () => {
    const key = requestKey(request);
    expect(requestKey({ ...request, selection: [] })).toBe(key);
    for (const change of [
      { provider: 'openai' },
      { description: 'Otra cosa' },
      { scope: 'selection' as const },
      { selection: ['obj-1'] },
      { baseRevision: 'rev-002' },
    ]) {
      expect(requestKey({ ...request, ...change })).not.toBe(key);
    }
  });
});

describe('RequestGate', () => {
  it('descarta respuestas tardías tras cancelar o empezar otra', () => {
    const gate = new RequestGate();
    const first = gate.start();
    gate.cancel();
    expect(first.signal.aborted).toBe(true);
    expect(gate.current(first.turn)).toBe(false);
    const second = gate.start();
    const third = gate.start();
    expect(second.signal.aborted).toBe(true);
    expect(gate.current(second.turn)).toBe(false);
    gate.finish(third.turn);
    expect(gate.busy).toBe(false);
  });
});

describe('AiClient', () => {
  it('una petición por propuesta, con tiempo límite propio', async () => {
    const fetchMock = vi.fn((_url: URL, init: RequestInit) => {
      expect(init.signal).toBeInstanceOf(AbortSignal);
      return Promise.resolve(new Response(JSON.stringify(plan([])), { status: 200 }));
    });
    vi.stubGlobal('fetch', fetchMock);
    await new AiClient('http://127.0.0.1:8765').propose('a'.repeat(32), request);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('las consultas cortas (proveedores, proyecto) también expiran', async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url: URL, init: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init.signal?.addEventListener('abort', () =>
              reject(new DOMException('timeout', 'TimeoutError')),
            );
          }),
      ),
    );
    const call = new AiClient('http://127.0.0.1:8765').project('a'.repeat(32));
    const assertion = expect(call).rejects.toMatchObject({ kind: 'timeout' });
    await vi.advanceTimersByTimeAsync(16_000);
    await assertion;
    vi.useRealTimers();
  });

  it('traduce los errores con su tipo y no reintenta', async () => {
    const body = JSON.stringify({ error: 'Obsoleto', fix: 'Pídelo', kind: 'stale_plan' });
    const fetchMock = vi.fn(() => Promise.resolve(new Response(body, { status: 409 })));
    vi.stubGlobal('fetch', fetchMock);
    const call = new AiClient('http://127.0.0.1:8765').apply(
      'a'.repeat(32),
      {
        planId: 'plan-0123456789abcdef',
        baseRevision: 'rev-001',
        operations: ['op1'],
        resolutions: {},
        inputs: {},
      },
      false,
    );
    await expect(call).rejects.toBeInstanceOf(AiRequestError);
    await call.catch((error: AiRequestError) => expect(error.kind).toBe('stale_plan'));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe('herramientas del visor', () => {
  const sun: SunState = {
    mode: 'datetime',
    isoDate: '2024-06-21T12:00:00+02:00',
    lat: 40.4168,
    lon: -3.7038,
    azimuthDeg: 180,
    elevationDeg: 45,
    northRotationDeg: 0,
    intensity: 3,
    colorTempK: 5800,
  };

  it('«sunset» es 20 minutos antes del ocaso calculado (no una hora inventada)', () => {
    const iso = resolveSunIso(sun, 'sunset', null);
    expect(iso.startsWith('2024-06-21T21:')).toBe(true); // ocaso en Madrid ≈ 21:48
    expect(resolveSunIso(sun, '18:30', '2024-12-01')).toBe('2024-12-01T18:30:00+02:00');
  });

  it('describe cada operación a partir de sus argumentos', () => {
    const name = (id: string): string => (id === 'brick_light' ? 'Ladrillo claro' : id);
    expect(
      describeOperation(
        operation({
          tool: 'building.set_height',
          args: { mode: 'delta_floors', value: 2, floorHeightM: null },
        }),
        name,
      ),
    ).toBe('+2 plantas');
    expect(
      describeOperation(
        operation({
          tool: 'material.assign',
          args: { surface: 'roof', materialId: 'brick_light' },
        }),
        name,
      ),
    ).toBe('Ladrillo claro en Cubierta');
  });
});
