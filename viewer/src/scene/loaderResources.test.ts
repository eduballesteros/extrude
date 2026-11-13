import { afterEach, describe, expect, it, vi } from 'vitest';
import { downloadResource, loadFromFile, loadFromManifest, loadFromUrl } from './loader';

const triangle = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
const document = {
  asset: { version: '2.0' },
  scene: 0,
  scenes: [{ nodes: [0] }],
  nodes: [{ mesh: 0 }],
  meshes: [{ primitives: [{ attributes: { POSITION: 0 } }] }],
  buffers: [{ uri: 'mesh.bin', byteLength: 36 }],
  bufferViews: [{ buffer: 0, byteLength: 36 }],
  accessors: [
    { bufferView: 0, componentType: 5126, count: 3, type: 'VEC3', min: [0, 0, 0], max: [1, 1, 0] },
  ],
};

afterEach(() => vi.unstubAllGlobals());

describe('progreso de descarga', () => {
  it('informa de fracciones crecientes hasta 1 con Content-Length', async () => {
    const chunks = [new Uint8Array(40), new Uint8Array(60)];
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const c of chunks) controller.enqueue(c);
        controller.close();
      },
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(body, { headers: { 'Content-Length': '100' } })),
    );
    const seen: (number | null)[] = [];
    const { bytes } = await downloadResource('/m.glb', (_, f) => seen.push(f));
    expect(bytes.byteLength).toBe(100);
    expect(seen).toEqual([0, 0.4, 1]);
  });

  it('sin Content-Length la fase es indeterminada', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new Uint8Array(10))));
    const seen: (number | null)[] = [];
    await downloadResource('/m.glb', (_, f) => seen.push(f));
    expect(seen.every((f) => f === null)).toBe(true);
  });

  it('error HTTP con mensaje claro', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 404 })));
    await expect(downloadResource('/no.glb')).rejects.toThrow('HTTP 404');
  });
});

function fetchResources(modelUrl: string, bufferUrl: string, redirected?: string) {
  vi.stubGlobal('location', { href: 'http://viewer.local/' });
  vi.stubGlobal('requestAnimationFrame', (cb: () => void) => setTimeout(cb, 0));
  vi.stubGlobal(
    'ProgressEvent',
    class {
      constructor(readonly type: string) {}
    },
  );
  const calls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((input: Request | string) => {
      const url = typeof input === 'string' ? input : input.url;
      calls.push(url);
      if (url === modelUrl) {
        const response = new Response(JSON.stringify(document));
        if (redirected) Object.defineProperty(response, 'url', { value: redirected });
        return Promise.resolve(response);
      }
      return Promise.resolve(
        url === bufferUrl
          ? new Response(triangle.buffer)
          : new Response('missing', { status: 404 }),
      );
    }),
  );
  return calls;
}

it('resuelve buffers glTF respecto al modelo, no respecto al visor', async () => {
  const url = 'http://models.local/site/model.gltf?version=2';
  const calls = fetchResources(url, 'http://models.local/site/mesh.bin');
  const model = await loadFromUrl(url);
  expect(model.root.children).toHaveLength(1);
  expect(calls).toEqual([url, 'http://models.local/site/mesh.bin']);
});

it('conserva la URL final tras una redirección del modelo', async () => {
  const url = 'http://models.local/model.gltf';
  const calls = fetchResources(
    url,
    'http://cdn.local/site/mesh.bin',
    'http://cdn.local/site/model.gltf',
  );
  await loadFromUrl(url);
  expect(calls).toEqual([url, 'http://cdn.local/site/mesh.bin']);
});

it('cada tesela conserva su propia base para recursos externos', async () => {
  const url = 'http://models.local/site/manifest.json';
  const tileUrl = 'http://models.local/site/tiles/tile_-2_0.glb';
  const calls = fetchResources(tileUrl, 'http://models.local/site/tiles/mesh.bin');
  const tileFetcher = fetch;
  vi.stubGlobal(
    'fetch',
    vi.fn((input: Request | string) => {
      if (input === url)
        return Promise.resolve(
          new Response(
            JSON.stringify({
              schemaVersion: 1,
              origin_offset: [0, 0, 0],
              units: 'm',
              crs: null,
              tile_size: 50,
              tiles: [
                {
                  id: '-2_0',
                  file: 'tiles/tile_-2_0.glb',
                  bbox: null,
                  triangles: 1,
                  size_bytes: 1,
                },
              ],
            }),
          ),
        );
      return tileFetcher(input);
    }),
  );
  const model = await loadFromManifest(url);
  expect(model.tiles).toBe(1);
  expect(calls).toEqual([tileUrl, 'http://models.local/site/tiles/mesh.bin']);
});

it('explica los recursos auxiliares de un glTF local sin buscarlos en el visor', async () => {
  const calls = fetchResources('unused', 'unused.bin');
  await expect(loadFromFile(new File([JSON.stringify(document)], 'model.gltf'))).rejects.toThrow(
    'glTF local referencia recursos externos',
  );
  expect(calls).toEqual([]);
});
