import { describe, expect, it, vi } from 'vitest';
import {
  ConversionClient,
  ConversionError,
  MAX_CONFIG_HEADER_BYTES,
  unreachableFix,
} from './client';

describe('unreachableFix', () => {
  it('en local propone arrancar el servidor', () => {
    expect(unreachableFix('http://127.0.0.1:8765')).toMatch(/uv run extrude serve/);
    expect(unreachableFix('http://localhost:8765')).toMatch(/uv run extrude serve/);
  });

  it('en un servidor web no da comandos', () => {
    const fix = unreachableFix('https://extrude.example.com');
    expect(fix).not.toMatch(/extrude serve|uv|cd /);
    expect(fix).toMatch(/administración/);
  });
});

describe('configuración YAML demasiado grande', () => {
  it('se rechaza en el visor con un mensaje claro, sin enviar nada', async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    const client = new ConversionClient('http://127.0.0.1:8765');
    const yaml = '# relleno\n'.repeat(Math.ceil(MAX_CONFIG_HEADER_BYTES / 10));
    const file = new File(['0'], 'plano.dxf');
    const error = await client.convert(file, yaml).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ConversionError);
    expect((error as ConversionError).message).toMatch(/demasiado grande/);
    expect(fetchSpy).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});
