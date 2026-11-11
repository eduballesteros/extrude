import { afterEach, describe, expect, it, vi } from 'vitest';
import { ConversionCancelledError, ConversionClient, ConversionTimeoutError } from './client';
import { ConversionSession } from './session';
import { download, ModelDownloadError } from '../scene/loader';

const client = new ConversionClient('http://127.0.0.1:8765');
const file = new File(['drawing'], 'plano.dxf');
const statusPath = '/api/jobs/abc/status';
const result = {
  job: 'abc',
  source: file.name,
  config_generated: false,
  files: {
    glb: '/api/jobs/abc/model.glb',
    manifest: null,
    report_md: '/report.md',
    report_json: '/report.json',
    config: '/config.yaml',
  },
  report: { warnings: [], output: { triangles: 10, total_size_bytes: 100 }, timings_s: {} },
};
const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status });
const accepted = (): Response => json({ status: statusPath }, 202);
const running = (): Response => json({ state: 'running', phase: 'build', fraction: 0.5 });
const done = (): Response => json({ state: 'done', result });

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('límites y cancelación de conversiones', () => {
  it('no envía un archivo si ya se canceló', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const controller = new AbortController();
    controller.abort();
    await expect(client.convert(file, null, { signal: controller.signal })).rejects.toBeInstanceOf(
      ConversionCancelledError,
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('cancela una subida bloqueada y aborta su fetch', async () => {
    const fetchMock = vi.fn(() => new Promise<Response>(() => {}));
    vi.stubGlobal('fetch', fetchMock);
    const controller = new AbortController();
    const promise = client.convert(file, null, { signal: controller.signal });
    const assertion = expect(promise).rejects.toBeInstanceOf(ConversionCancelledError);
    controller.abort();
    await assertion;
    const init = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init[1].signal?.aborted).toBe(true);
  });

  it('limita también una respuesta cuyo JSON no termina de llegar', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ json: () => new Promise(() => {}) }));
    const assertion = expect(
      client.convert(file, null, { requestTimeoutMs: 100 }),
    ).rejects.toBeInstanceOf(ConversionTimeoutError);
    await vi.advanceTimersByTimeAsync(100);
    await assertion;
    expect(vi.getTimerCount()).toBe(0);
  });

  it('el límite total termina un trabajo que responde pero nunca acaba', async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(accepted())
        .mockImplementation(() => Promise.resolve(running())),
    );
    const assertion = expect(
      client.convert(file, null, { timeoutMs: 250, pollMs: 50 }),
    ).rejects.toBeInstanceOf(ConversionTimeoutError);
    await vi.advanceTimersByTimeAsync(250);
    await assertion;
    expect(vi.getTimerCount()).toBe(0);
  });

  it('cancela la pausa entre sondeos sin esperar al siguiente', async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(accepted())
      .mockImplementation(() => Promise.resolve(running()));
    vi.stubGlobal('fetch', fetchMock);
    const assertion = expect(
      client.convert(file, null, { signal: controller.signal, pollMs: 10_000 }),
    ).rejects.toBeInstanceOf(ConversionCancelledError);
    await vi.advanceTimersByTimeAsync(0);
    controller.abort();
    await assertion;
    await vi.advanceTimersByTimeAsync(20_000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('limpia los temporizadores al terminar y rechaza estados incompletos', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(accepted()).mockResolvedValueOnce(done()));
    await expect(client.convert(file, null)).resolves.toEqual(result);
    expect(vi.getTimerCount()).toBe(0);
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValueOnce(accepted()).mockResolvedValueOnce(json({})),
    );
    await expect(client.convert(file, null)).rejects.toThrow(/estado incompleto/);
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('recuperar el mismo trabajo', () => {
  it('vuelve a convertir si los archivos del resultado han caducado', async () => {
    const session = new ConversionSession(file, null);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(accepted())
      .mockResolvedValueOnce(done())
      .mockResolvedValueOnce(new Response('', { status: 404 }))
      .mockResolvedValueOnce(accepted())
      .mockResolvedValueOnce(done());
    vi.stubGlobal('fetch', fetchMock);
    const converted = await session.run(client, {});
    try {
      await download(client.url(converted.files.glb!));
      throw new Error('Se esperaba un resultado caducado');
    } catch (error) {
      expect(error).toBeInstanceOf(ModelDownloadError);
      if (!(error instanceof ModelDownloadError) || error.status !== 404) throw error;
      session.discardExpiredResult();
    }
    expect(session.retryLabel).toBe('Reintentar envío');
    await session.run(client, {});
    expect(
      fetchMock.mock.calls.filter(
        ([, init]) => (init as RequestInit | undefined)?.method === 'POST',
      ),
    ).toHaveLength(2);
  });
  it('tras cancelar retoma con GET y conserva el archivo y YAML originales', async () => {
    const controller = new AbortController();
    const session = new ConversionSession(file, 'layers: []');
    const fetchMock = vi.fn().mockResolvedValueOnce(accepted()).mockResolvedValueOnce(done());
    vi.stubGlobal('fetch', fetchMock);
    await expect(
      session.run(client, { signal: controller.signal, onAccepted: () => controller.abort() }),
    ).rejects.toBeInstanceOf(ConversionCancelledError);
    expect(session.retryLabel).toBe('Retomar espera');
    await expect(session.run(client, {})).resolves.toEqual(result);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1]![0]).toBe(client.url(statusPath));
    const init = fetchMock.mock.calls[1]![1] as RequestInit;
    expect(init.method).toBeUndefined();
    expect(init.body).toBeUndefined();
    await session.run(client, {}); // fallo posterior al descargar: reutilizar el resultado
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(session.retryLabel).toBe('Reabrir resultado');
  });

  it.each([404, 200])('permite reenviar si el trabajo expiró o falló (%s)', async (code) => {
    const session = new ConversionSession(file, null);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(accepted())
      .mockResolvedValueOnce(
        code === 404 ? json({}, 404) : json({ state: 'failed', error: 'DXF roto' }),
      )
      .mockResolvedValueOnce(accepted())
      .mockResolvedValueOnce(done());
    vi.stubGlobal('fetch', fetchMock);
    await expect(session.run(client, {})).rejects.toThrow();
    expect(session.retryLabel).toBe('Reintentar envío');
    await expect(session.run(client, {})).resolves.toEqual(result);
    expect(
      fetchMock.mock.calls.filter(([, init]) => (init as RequestInit).method === 'POST'),
    ).toHaveLength(2);
  });

  it('si se cae la red durante el sondeo conserva el identificador', async () => {
    const session = new ConversionSession(file, null);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(accepted())
      .mockRejectedValueOnce(new TypeError('offline'))
      .mockResolvedValueOnce(done());
    vi.stubGlobal('fetch', fetchMock);
    await expect(session.run(client, {})).rejects.toThrow(/contactar/);
    expect(session.retryLabel).toBe('Retomar espera');
    await expect(session.run(client, {})).resolves.toEqual(result);
    expect(
      fetchMock.mock.calls.filter(([, init]) => (init as RequestInit).method === 'POST'),
    ).toHaveLength(1);
  });
});
