import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

let session: typeof import('./session');
let storage: Map<string, string>;

beforeEach(async () => {
  vi.resetModules();
  storage = new Map();
  vi.stubGlobal('sessionStorage', {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  });
  session = await import('./session');
});

describe('sesión del servidor compartido', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('añade la sesión solo a las peticiones al servidor y avisa si caduca', async () => {
    const seen: (string | null)[] = [];
    const base = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      seen.push(new Headers(init?.headers).get('Authorization'));
      const expired = (_input instanceof Request ? _input.url : String(_input)).includes(
        '/api/library',
      );
      return Promise.resolve(
        new Response(JSON.stringify(expired ? { kind: 'auth_required' } : {}), {
          status: expired ? 401 : 200,
        }),
      );
    });
    vi.stubGlobal('fetch', base);
    globalThis.fetch = base;
    session.installSessionFetch('http://127.0.0.1:8765');
    session.setSessionToken('t-123');
    let asked = 0;
    const off = session.onAuthRequired(() => (asked += 1));
    await fetch('https://cdn.example.org/x.js');
    await fetch('http://127.0.0.1:8765/api/health');
    await fetch('http://127.0.0.1:8765/api/library');
    expect(seen).toEqual([null, 'Bearer t-123', 'Bearer t-123']);
    expect(asked).toBe(1);
    expect(session.sessionToken()).toBeNull();
    off();
  });

  it('separa los tokens por servidor y no envía el de A al cambiar a B', async () => {
    const seen: (string | null)[] = [];
    const base = vi.fn((_url: RequestInfo | URL, init?: RequestInit) => {
      seen.push(new Headers(init?.headers).get('Authorization'));
      return Promise.resolve(new Response('{}'));
    });
    vi.stubGlobal('fetch', base);
    session.installSessionFetch('https://a.example');
    session.setSessionToken('a-token');
    session.installSessionFetch('https://b.example');
    await session.sessionInfo('https://b.example');
    expect(session.sessionToken()).toBeNull();
    session.setSessionToken('b-token');
    await session.sessionInfo('https://b.example');
    session.installSessionFetch('https://a.example');
    await session.sessionInfo('https://a.example');
    expect(seen).toEqual([null, 'Bearer b-token', 'Bearer a-token']);
    expect(base).toHaveBeenCalledTimes(3); // instalar otra vez no encadena interceptores
  });

  it('descarta el token antiguo que no identifica a su servidor', async () => {
    storage.set('extrude.session', 'legacy-secret');
    const base = vi.fn<typeof fetch>().mockResolvedValue(new Response('{}'));
    vi.stubGlobal('fetch', base);
    session.installSessionFetch('https://new.example');
    await session.sessionInfo('https://new.example');
    expect(new Headers(base.mock.calls[0]![1]?.headers).has('Authorization')).toBe(false);
    expect(storage.has('extrude.session')).toBe(false);
  });

  it('un 401 tardío de A no cierra la sesión nueva de B y conserva las cabeceras de Request', async () => {
    let complete!: (response: Response) => void;
    const base = vi.fn((_url: RequestInfo | URL, init?: RequestInit) => {
      expect(new Headers(init?.headers).get('X-Test')).toBe('original');
      return new Promise<Response>((resolve) => (complete = resolve));
    });
    vi.stubGlobal('fetch', base);
    session.installSessionFetch('https://a.example');
    session.setSessionToken('a-token');
    const pending = fetch(
      new Request('https://a.example/api/library', { headers: { 'X-Test': 'original' } }),
    );
    session.installSessionFetch('https://b.example');
    session.setSessionToken('b-token');
    complete(new Response(JSON.stringify({ kind: 'auth_required' }), { status: 401 }));
    await pending;
    expect(session.sessionToken()).toBe('b-token');
  });
});
