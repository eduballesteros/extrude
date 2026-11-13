import { afterEach, expect, it, vi } from 'vitest';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.resetModules();
});

it('usa el mismo origen en el build para usuarios remotos', async () => {
  vi.stubEnv('PROD', true);
  vi.stubGlobal('location', { origin: 'https://extrude.example.com' });
  vi.resetModules();
  const { DEFAULT_SERVER } = await import('./client');
  expect(DEFAULT_SERVER).toBe('https://extrude.example.com');
});

it('conserva el servidor local en desarrollo', async () => {
  vi.stubEnv('PROD', false);
  vi.stubGlobal('location', { origin: 'http://localhost:5173' });
  vi.resetModules();
  const { DEFAULT_SERVER } = await import('./client');
  expect(DEFAULT_SERVER).toBe('http://127.0.0.1:8765');
});
