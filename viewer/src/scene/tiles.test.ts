import type { Object3D } from 'three';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { LayerManager } from './layers';
import {
  isManifestUrl,
  loadFromManifest,
  TILE_LOOKAHEAD,
  type Manifest,
  manifestExtras,
  parseManifest,
  unionBbox,
} from './loader';

afterEach(() => vi.unstubAllGlobals());

/** GLB mínimo (solo bloque JSON) con un nodo por capa, como una tesela del pipeline. */
function tileGlb(layers: Record<string, number>): ArrayBuffer {
  const names = Object.keys(layers);
  const doc = {
    asset: { version: '2.0' },
    scene: 0,
    scenes: [{ nodes: names.map((_, i) => i), extras: { origin_offset: [1, 2, 0] } }],
    nodes: names.map((name) => ({
      name,
      extras: { layer: name, kind: 'building', visible: true, triangle_count: layers[name] },
    })),
  };
  const raw = new TextEncoder().encode(JSON.stringify(doc));
  const json = new Uint8Array(Math.ceil(raw.length / 4) * 4).fill(0x20);
  json.set(raw);
  const glb = new ArrayBuffer(12 + 8 + json.length);
  const view = new DataView(glb);
  view.setUint32(0, 0x46546c67, true); // 'glTF'
  view.setUint32(4, 2, true);
  view.setUint32(8, glb.byteLength, true);
  view.setUint32(12, json.length, true);
  view.setUint32(16, 0x4e4f534a, true); // 'JSON'
  new Uint8Array(glb, 20).set(json);
  return glb;
}

function manifest(overrides: Partial<Manifest> = {}): Manifest {
  return {
    schemaVersion: 1,
    origin_offset: [230032.5, 4140015, 0],
    units: 'm',
    crs: 'EPSG:25830',
    center_lat: 37.3676,
    center_lon: -6.0485,
    tile_size: 25,
    tiles: [
      {
        id: '0_0',
        file: 'tiles/tile_0_0.glb',
        bbox: { min: [-30, 0, -10], max: [-5, 12, 10] },
        triangles: 12,
        size_bytes: 100,
      },
      {
        id: '1_0',
        file: 'tiles/tile_1_0.glb',
        bbox: { min: [-5, 0, -15], max: [30, 20, 5] },
        triangles: 30,
        size_bytes: 300,
      },
    ],
    ...overrides,
  };
}

describe('manifest.json', () => {
  it.each([
    ['units', 'ft'],
    ['crs', 12],
    ['tile_size', 0],
    ['tile_size', Infinity],
    ['center_lat', 91],
    ['center_lon', NaN],
    ['geometry_revision', 'wrong-revision'],
  ])('rechaza el campo global %s inválido', (field, value) => {
    expect(() => parseManifest({ ...manifest(), [field]: value })).toThrow(String(field));
  });

  it.each([
    ['bbox', undefined],
    ['bbox', { min: [0, 0, 0], max: [-1, 1, 1] }],
    ['bbox', { min: [0, 0, 0], max: [1, Infinity, 1] }],
    ['triangles', -1],
    ['triangles', 1.5],
    ['size_bytes', '100'],
    ['size_bytes', NaN],
    ['id', ''],
    ['id', '../bad'],
  ])('rechaza tiles[0].%s inválido antes de cargar', (field, value) => {
    const m = manifest();
    Object.assign(m.tiles[0]!, { [field]: value });
    expect(() => parseManifest(m)).toThrow(`tiles[0].${field}`);
  });

  it('rechaza IDs y archivos duplicados y admite bbox nulo', () => {
    const ids = manifest();
    ids.tiles[1]!.id = ids.tiles[0]!.id;
    expect(() => parseManifest(ids)).toThrow('tiles[1].id');
    const files = manifest();
    files.tiles[1]!.file = files.tiles[0]!.file;
    expect(() => parseManifest(files)).toThrow('tiles[1].file');
    const m = manifest();
    m.tiles.forEach((t) => {
      t.bbox = null;
    });
    expect(manifestExtras(parseManifest(m)).bbox).toBeNull();
  });
  it('valida versión, origen y rutas de teselas (sin salir de tiles/)', () => {
    expect(parseManifest(manifest()).tiles).toHaveLength(2);
    expect(() => parseManifest(null)).toThrow();
    expect(() => parseManifest(manifest({ schemaVersion: 2 }))).toThrow(/Versión/);
    expect(() => parseManifest(manifest({ tiles: [] }))).toThrow(/teselas/);
    for (const file of ['../input.dxf', 'tiles/../x.glb', '/etc/tile.glb', 'tiles/a.txt']) {
      const bad = manifest();
      bad.tiles[0]!.file = file;
      expect(() => parseManifest(bad)).toThrow(/Ruta de tesela/);
    }
  });

  it('extras del conjunto: origen y CRS comunes, bbox que envuelve todas las teselas', () => {
    expect(unionBbox(manifest().tiles)).toEqual({ min: [-30, 0, -15], max: [30, 20, 10] });
    const extras = manifestExtras(manifest());
    expect(extras.origin_offset).toEqual([230032.5, 4140015, 0]);
    expect(extras).toMatchObject({ crs: 'EPSG:25830', center_lat: 37.3676, center_lon: -6.0485 });
  });

  it('conserva la identidad geométrica del manifest para habilitar la edición del proyecto', () => {
    const revision = `sha256:${'a'.repeat(64)}`;
    const extras = manifestExtras(parseManifest(manifest({ geometry_revision: revision })));
    expect(extras.geometry_revision).toBe(revision);
  });

  it('reconoce URLs de manifest', () => {
    expect(isManifestUrl('http://127.0.0.1:8765/api/jobs/abc/manifest.json')).toBe(true);
    expect(isManifestUrl('/out/manifest.json?v=2')).toBe(true);
    expect(isManifestUrl('/models/city.glb')).toBe(false);
    expect(isManifestUrl('/manifest.json.glb')).toBe(false);
  });
});

describe('loadFromManifest', () => {
  const base = 'http://127.0.0.1:8765/api/jobs/abc/';

  it('descarga las teselas relativas al manifest y agrupa sus raíces con capas unificadas', async () => {
    const bodies: Record<string, ArrayBuffer | string> = {
      [`${base}manifest.json`]: JSON.stringify(
        manifest({ geometry_revision: `sha256:${'b'.repeat(64)}` }),
      ),
      [`${base}tiles/tile_0_0.glb`]: tileGlb({ EDIFICIOS: 12 }),
      [`${base}tiles/tile_1_0.glb`]: tileGlb({ EDIFICIOS: 20, VIALES: 10 }),
    };
    const fetchMock = vi.fn((url: string) =>
      Promise.resolve(
        url in bodies ? new Response(bodies[url]) : new Response('no', { status: 404 }),
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('requestAnimationFrame', (cb: () => void) => setTimeout(cb, 0));
    const stages: string[] = [];

    const model = await loadFromManifest(`${base}manifest.json`, 'ciudad', (stage) => {
      stages.push(stage);
    });

    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual(Object.keys(bodies));
    expect(model.tiles).toBe(2);
    expect(model.bytes).toBeNull();
    expect(model.extras?.crs).toBe('EPSG:25830');
    expect(model.extras?.geometry_revision).toBe(`sha256:${'b'.repeat(64)}`);
    expect(model.root.children.map((c: Object3D) => c.name)).toEqual(['0_0', '1_0']);
    expect(stages).toContain('download');
    expect(stages).toContain('parse');

    const layers = new LayerManager();
    layers.attach(model.root);
    const edificios = layers.layers.get('EDIFICIOS')!;
    expect(edificios.nodes).toHaveLength(2);
    expect(edificios.triangles).toBe(32);
    layers.apply({ EDIFICIOS: { visible: false }, VIALES: { visible: true } });
    expect(edificios.nodes.every((n) => !n.visible)).toBe(true);
    expect(layers.layers.get('VIALES')!.nodes[0]!.visible).toBe(true);
  });

  it('descarga en paralelo sin retener más de TILE_LOOKAHEAD teselas a la vez', async () => {
    const count = 10;
    const tiles = Array.from({ length: count }, (_, i) => ({
      id: `${i}_0`,
      file: `tiles/tile_${i}_0.glb`,
      bbox: null,
      triangles: 1,
      size_bytes: 100,
    }));
    let inFlight = 0;
    let maxInFlight = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.endsWith('manifest.json'))
          return new Response(JSON.stringify({ ...manifest(), tiles }));
        inFlight++;
        maxInFlight = Math.max(maxInFlight, inFlight);
        await new Promise((r) => setTimeout(r, 5));
        inFlight--;
        return new Response(tileGlb({ EDIFICIOS: 1 }));
      }),
    );
    vi.stubGlobal('requestAnimationFrame', (cb: () => void) => setTimeout(cb, 0));
    const progress: [string, number | null][] = [];

    const model = await loadFromManifest(`${base}manifest.json`, 'ciudad', (stage, fraction) => {
      progress.push([stage, fraction]);
    });

    expect(model.root.children.map((c: Object3D) => c.name)).toEqual(tiles.map((t) => t.id));
    expect(maxInFlight).toBeGreaterThan(1);
    expect(maxInFlight).toBeLessThanOrEqual(TILE_LOOKAHEAD);
    const downloads = progress
      .filter(([s, f]) => s === 'download' && f !== null)
      .map(([, f]) => f!);
    expect(downloads.every((f, i) => i === 0 || f >= downloads[i - 1]!)).toBe(true);
    expect(progress.at(-1)).toEqual(['parse', 1]);
  });

  it('cancela las demás descargas si una tesela falla', async () => {
    const tiles = Array.from({ length: 6 }, (_, i) => ({
      id: `${i}_0`,
      file: `tiles/tile_${i}_0.glb`,
      bbox: null,
      triangles: 1,
      size_bytes: 1,
    }));
    const aborted: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string, init?: RequestInit) => {
        if (url.endsWith('manifest.json'))
          return Promise.resolve(new Response(JSON.stringify({ ...manifest(), tiles })));
        if (url.includes('tile_0_0')) return Promise.resolve(new Response('no', { status: 404 }));
        return new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => {
            aborted.push(url);
            reject(new DOMException('cancelada', 'AbortError'));
          });
        });
      }),
    );
    await expect(loadFromManifest(`${base}manifest.json`)).rejects.toThrow(/HTTP 404/);
    expect(aborted.length).toBe(TILE_LOOKAHEAD - 1);
  });

  it('falla con un mensaje claro si falta una tesela', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) =>
        Promise.resolve(
          url.endsWith('manifest.json')
            ? new Response(JSON.stringify(manifest()))
            : new Response('no', { status: 404 }),
        ),
      ),
    );
    await expect(loadFromManifest(`${base}manifest.json`)).rejects.toThrow(/HTTP 404/);
  });
});
