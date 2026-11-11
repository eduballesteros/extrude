import { afterEach, describe, expect, it, vi } from 'vitest';
import { stageLabel } from '../ui/welcome';
import { ConversionClient, ConversionError } from './client';

const SERVER = 'http://127.0.0.1:8765';
const STATUS = '/api/jobs/abc/status';
const RESULT = {
  job: 'abc',
  source: 'plano.dxf',
  config_generated: false,
  files: {
    glb: '/api/jobs/abc/model.glb',
    manifest: null,
    report_md: '/api/jobs/abc/report.md',
    report_json: '/api/jobs/abc/report.json',
    config: '/api/jobs/abc/config.yaml',
  },
  report: { warnings: [], output: { triangles: 12, total_size_bytes: 100 }, timings_s: {} },
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

/** Servidor simulado: 202 al enviar y luego una secuencia de estados. */
function serverWith(statuses: unknown[]) {
  const queue = [...statuses];
  return vi.fn((url: string) => {
    if (url.includes('/api/convert')) {
      return Promise.resolve(json({ job: 'abc', state: 'pending', status: STATUS }, 202));
    }
    expect(url).toBe(SERVER + STATUS);
    return Promise.resolve(json(queue.length > 1 ? queue.shift() : queue[0]));
  });
}

afterEach(() => vi.unstubAllGlobals());

describe('conversión asíncrona con progreso por fases', () => {
  it('sondea el estado, informa cada fase y devuelve el resultado', async () => {
    const fetchMock = serverWith([
      { state: 'pending', phase: 'queued', fraction: 0 },
      { state: 'running', phase: 'draft_config', fraction: 0 },
      { state: 'running', phase: 'read', fraction: 0.05 },
      { state: 'running', phase: 'read', fraction: 0.05 },
      { state: 'running', phase: 'build', fraction: 0.5 },
      { state: 'running', phase: 'export', fraction: 0.88 },
      { state: 'done', phase: 'done', fraction: 1, result: RESULT },
    ]);
    vi.stubGlobal('fetch', fetchMock);
    const seen: [string, number | null][] = [];
    const client = new ConversionClient(SERVER);
    const result = await client.convert(new File(['x'], 'plano.dxf'), null, {
      pollMs: 0,
      onProgress: (phase, fraction) => seen.push([phase, fraction]),
    });
    expect(result).toEqual(RESULT);
    expect(seen).toEqual([
      ['upload', null],
      ['queued', null], // en cola: sin porcentaje
      ['draft_config', null], // borrador del YAML: sin avance medible
      ['read', 0.05], // la repetición no se vuelve a notificar
      ['build', 0.5],
      ['export', 0.88],
    ]);
  });

  it('un fallo del trabajo llega como ConversionError con su solución', async () => {
    vi.stubGlobal(
      'fetch',
      serverWith([
        { state: 'running', phase: 'read', fraction: 0.05 },
        {
          state: 'failed',
          phase: 'failed',
          fraction: 0.05,
          error: "'roto.dxf' no es un DXF válido.",
          fix: 'Expórtalo de nuevo.',
          http_status: 422,
        },
      ]),
    );
    const promise = new ConversionClient(SERVER).convert(new File(['x'], 'roto.dxf'), null, {
      pollMs: 0,
    });
    await expect(promise).rejects.toThrow(ConversionError);
    await expect(promise).rejects.toMatchObject({ fix: 'Expórtalo de nuevo.' });
  });

  it('si el servidor deja de responder durante el sondeo, el error lo dice', async () => {
    let calls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        calls++ === 0
          ? Promise.resolve(json({ job: 'abc', state: 'pending', status: STATUS }, 202))
          : Promise.reject(new TypeError('Failed to fetch')),
      ),
    );
    const promise = new ConversionClient(SERVER).convert(new File(['x'], 'a.dxf'), null, {
      pollMs: 0,
    });
    await expect(promise).rejects.toThrow(/No se pudo contactar/);
  });

  it('un servidor antiguo que responde 200 síncrono sigue funcionando', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(RESULT));
    vi.stubGlobal('fetch', fetchMock);
    const result = await new ConversionClient(SERVER).convert(new File(['x'], 'a.dxf'), null);
    expect(result).toEqual(RESULT);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe('textos de la barra de progreso', () => {
  it('cada fase del servidor tiene un texto claro; las desconocidas, el genérico', () => {
    expect(stageLabel('convert:read')).toBe('Leyendo entidades…');
    expect(stageLabel('convert:build')).toBe('Construyendo geometría 3D…');
    expect(stageLabel('convert:export')).toBe('Exportando GLB…');
    expect(stageLabel('convert:fase_futura')).toBe('Convirtiendo plano (extrude serve)');
    expect(stageLabel('download')).toBe('Descargando');
  });
});
