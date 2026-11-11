import { afterEach, describe, expect, it, vi } from 'vitest';
import { CONFIG_HEADER, ConversionClient, DEFAULT_SERVER } from './client';
import { ConversionDestination } from './destination';

afterEach(() => vi.unstubAllGlobals());

function setup(query: string | null, configured?: string) {
  const destination = new ConversionDestination(query, configured);
  const client = new ConversionClient(destination.server, () => destination.assertUploadAllowed());
  const fetchMock = vi
    .fn()
    .mockResolvedValue(new Response(JSON.stringify({ job: 'test', files: {} }), { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  return { destination, client, fetchMock };
}

describe('destino de conversión', () => {
  it('bloquea el File y el YAML hasta aceptar el servidor remoto indicado por URL', async () => {
    const { destination, client, fetchMock } = setup('https://remote.example');
    const file = new File(['plano confidencial'], 'proyecto.dwg');
    expect(destination.requiresAcceptance).toBe(true);
    expect(destination.description).toContain('servidor remoto');
    expect(destination.description).not.toContain('en tu equipo');
    await expect(client.convert(file, 'layers: []')).rejects.toThrow('https://remote.example');
    expect(fetchMock).not.toHaveBeenCalled();

    destination.setAccepted(true);
    await client.convert(file, 'layers: []');
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(new URL(url).origin).toBe(destination.server);
    expect(init.body).toBe(file);
    expect((init.headers as Record<string, string>)[CONFIG_HEADER]).toBeTruthy();

    destination.setAccepted(false);
    await expect(client.convert(file, null)).rejects.toThrow('https://remote.example');
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(() =>
      new ConversionDestination('https://remote.example').assertUploadAllowed(),
    ).toThrow();
  });

  it.each([null, DEFAULT_SERVER, 'http://localhost:8765', 'http://[::1]:8765'])(
    'muestra y permite el destino local %s',
    async (query) => {
      const { destination, client, fetchMock } = setup(query);
      expect(destination.local).toBe(true);
      expect(destination.requiresAcceptance).toBe(false);
      expect(destination.description).toContain('en tu equipo');
      await client.convert(new File(['dxf'], 'plano.dxf'), null);
      expect(fetchMock).toHaveBeenCalledOnce();
    },
  );

  it('muestra el servidor remoto de confianza y exige aceptación si el enlace lo sustituye', () => {
    const configured = new ConversionDestination(null, 'https://trusted.example:443/');
    expect(configured.server).toBe('https://trusted.example');
    expect(configured.description).toContain('servidor remoto');
    expect(() => configured.assertUploadAllowed()).not.toThrow();
    const same = new ConversionDestination(
      'https://trusted.example',
      'https://trusted.example:443/',
    );
    expect(same.requiresAcceptance).toBe(false);
    const override = new ConversionDestination('https://other.example', 'https://trusted.example');
    expect(override.requiresAcceptance).toBe(true);
    expect(() => override.assertUploadAllowed()).toThrow();
  });

  it.each([
    'http://localhost.remote.example',
    'https://127.0.0.1.remote.example',
    'http://192.168.1.10',
  ])('no trata %s como conversión en tu equipo', (query) => {
    const { destination } = setup(query);
    expect(destination.local).toBe(false);
    expect(destination.requiresAcceptance).toBe(true);
  });

  it.each([
    '',
    'remote.example',
    '//remote.example',
    'ftp://remote.example',
    'https://user:secret@remote.example',
    'javascript:alert(1)',
  ])('bloquea un destino inválido antes de enviar: %s', async (query) => {
    const { destination, client, fetchMock } = setup(query);
    expect(destination.error).not.toBeNull();
    destination.setAccepted(true);
    await expect(client.convert(new File(['dxf'], 'plano.dxf'), null)).rejects.toThrow('no válido');
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
