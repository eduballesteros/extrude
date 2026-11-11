import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  CONFIG_HEADER,
  ConversionClient,
  ConversionError,
  convertedModel,
  hasExtension,
} from './client';

describe('teselas', () => {
  const client = new ConversionClient('http://127.0.0.1:8765');
  afterEach(() => vi.unstubAllGlobals());

  it('pide la conversión teselada con ?tiles= y abre el manifest', async () => {
    const files = {
      glb: null,
      manifest: '/api/jobs/abc/manifest.json',
      report_md: '/api/jobs/abc/report.md',
      report_json: '/api/jobs/abc/report.json',
      config: '/api/jobs/abc/config.yaml',
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ job: 'abc', files }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const result = await client.convert(new File(['x'], 'plano 1.dxf'), null, { tiles: 500 });
    expect(fetchMock.mock.calls[0]![0]).toBe(
      'http://127.0.0.1:8765/api/convert?name=plano%201.dxf&tiles=500&async=1',
    );
    expect(convertedModel(result)).toEqual({ kind: 'tiles', url: '/api/jobs/abc/manifest.json' });
    expect(convertedModel({ ...result, files: { ...files, glb: '/m.glb' } })).toEqual({
      kind: 'glb',
      url: '/m.glb',
    });
    expect(convertedModel({ ...result, files: { ...files, manifest: null } })).toBeNull();
  });

  it.each([0, NaN, Infinity, -Infinity])('rechaza el tamaño de tesela %s', async (tiles) => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const file = new File(['x'], 'plano.dxf');
    await expect(client.convert(file, null, { tiles })).rejects.toThrow(/tesela/);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

const client = new ConversionClient('http://127.0.0.1:8765');

afterEach(() => vi.unstubAllGlobals());

describe('ConversionClient', () => {
  it('solo acepta .dwg o .dxf', async () => {
    await expect(client.convert(new File(['x'], 'modelo.glb'), null)).rejects.toThrow(
      ConversionError,
    );
    expect(hasExtension('PLANO.DWG', ['.dwg', '.dxf'])).toBe(true);
    expect(hasExtension('plano.dxf.zip', ['.dwg', '.dxf'])).toBe(false);
  });

  it('envía el plano en el cuerpo y el YAML en Base64 UTF-8', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ job: 'a'.repeat(32), files: {} }), { status: 200 }),
      );
    vi.stubGlobal('fetch', fetchMock);
    const file = new File(['0\nEOF\n'], 'Manzana Ñ.dxf');
    await client.convert(file, 'layers:\n  - match: "EDIFICACIÓN"\n    kind: building\n');
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://127.0.0.1:8765/api/convert?name=Manzana%20%C3%91.dxf&async=1');
    expect(init.body).toBe(file);
    const header = (init.headers as Record<string, string>)[CONFIG_HEADER]!;
    const decoded = new TextDecoder().decode(Uint8Array.from(atob(header), (c) => c.charCodeAt(0)));
    expect(decoded).toContain('EDIFICACIÓN');
  });

  it('sin YAML no envía la cabecera (el servidor genera un borrador)', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    await client.convert(new File(['x'], 'a.dwg'), null);
    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    expect(init.headers).not.toHaveProperty(CONFIG_HEADER);
  });

  it('traduce los errores del servidor con su solución', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: 'Formato no admitido', fix: 'Usa .dwg o .dxf.' }), {
          status: 415,
        }),
      ),
    );
    await expect(client.convert(new File(['x'], 'a.dxf'), null)).rejects.toMatchObject({
      message: 'Formato no admitido',
      fix: 'Usa .dwg o .dxf.',
    });
  });

  it('si el servidor no responde, explica cómo arrancarlo', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    await expect(client.convert(new File(['x'], 'a.dxf'), null)).rejects.toMatchObject({
      fix: expect.stringContaining('extrude serve') as unknown,
    });
  });
});
