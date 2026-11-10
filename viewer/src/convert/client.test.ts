import { afterEach, describe, expect, it, vi } from 'vitest';
import { CONFIG_HEADER, ConversionClient, ConversionError, hasExtension } from './client';

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
    expect(url).toBe('http://127.0.0.1:8765/api/convert?name=Manzana%20%C3%91.dxf');
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
