import { describe, expect, it, vi } from 'vitest';
import { fitSide } from './bitmap';
import {
  captureSize,
  imageFilename,
  ImageLibrary,
  type ImageStorage,
  memoryStorage,
  type NewImage,
  resolutionLabel,
} from './images';

function image(overrides: Partial<NewImage> = {}): NewImage {
  return {
    project: 'manzana.glb',
    viewId: 'v1',
    viewName: 'Vista 1',
    kind: 'capture',
    width: 1920,
    height: 1080,
    mime: 'image/png',
    filename: 'x.png',
    thumbnail: '',
    ...overrides,
  };
}

const blob = (text = 'png'): Blob => new Blob([text], { type: 'image/png' });

describe('captureSize', () => {
  it('HD y 4K exactos para vistas 16:9', () => {
    expect(captureSize(3840, 2160, 'HD')).toEqual({ width: 1920, height: 1080 });
    expect(captureSize(1280, 720, '4K')).toEqual({ width: 3840, height: 2160 });
  });

  it('conserva la proporción de la vista cuando no es 16:9', () => {
    expect(captureSize(2880, 2160, 'HD')).toEqual({ width: 1440, height: 1080 }); // 4:3
    expect(captureSize(2048, 2048, '4K')).toEqual({ width: 2160, height: 2160 }); // 1:1
    expect(captureSize(4961, 3508, '4K')).toEqual({ width: 3054, height: 2160 }); // A3
    expect(captureSize(2560, 1080, 'HD')).toEqual({ width: 1920, height: 810 }); // 21:9
  });
});

describe('nombres y etiquetas', () => {
  const date = new Date(2026, 10, 12, 9, 5, 7);

  it('incluyen proyecto, vista, tipo y resolución', () => {
    const base = { project: 'Manzana Norte.glb', view: 'Vista 1: acceso', date, mime: 'image/png' };
    expect(imageFilename({ ...base, kind: 'capture', width: 3840, height: 2160 })).toBe(
      'Manzana-Norte_Vista-1-acceso_captura_3840x2160_20261112-090507.png',
    );
    expect(
      imageFilename({ ...base, kind: 'ai', width: 1536, height: 1024, mime: 'image/jpeg' }),
    ).toBe('Manzana-Norte_Vista-1-acceso_ia_1536x1024_20261112-090507.jpg');
  });

  it('una ampliación y una imagen de prueba se nombran como lo que son', () => {
    const base = { project: 'p', view: 'v', date, mime: 'image/png', width: 3840, height: 2160 };
    expect(imageFilename({ ...base, kind: 'ai', upscaled: true })).toContain('_ia-ampliada_');
    expect(imageFilename({ ...base, kind: 'ai', simulated: true })).toContain('_prueba_');
    expect(imageFilename({ ...base, kind: 'ai', simulated: true, upscaled: true })).toContain(
      '_prueba-ampliada_',
    );
  });

  it('la etiqueta de resolución sale de las dimensiones reales', () => {
    expect(resolutionLabel(3840, 2160)).toBe('4K');
    expect(resolutionLabel(1920, 1080)).toBe('HD');
    expect(resolutionLabel(1440, 1080)).toBe('HD');
    expect(resolutionLabel(1536, 1024)).toBe('');
  });

  it('la referencia para la IA se reduce a 2048 px de lado, sin ampliar', () => {
    expect(fitSide(3840, 2160)).toEqual({ width: 2048, height: 1152 });
    expect(fitSide(1920, 1080)).toEqual({ width: 1920, height: 1080 });
    expect(fitSide(1080, 4000)).toEqual({ width: 553, height: 2048 });
  });
});

describe('ImageLibrary', () => {
  it('añade sin sustituir: dos capturas de la misma vista conviven con su procedencia', async () => {
    const library = new ImageLibrary(memoryStorage());
    await library.open('manzana.glb');
    const first = await library.add(image({ modelRevision: 'rev-a' }), blob('1'));
    const second = await library.add(image({ modelRevision: 'rev-b' }), blob('2'));
    expect(first.id).not.toBe(second.id);
    expect(library.forView('v1').map((i) => i.modelRevision)).toEqual(['rev-a', 'rev-b']);
    expect(await (await library.blob(first.id))?.text()).toBe('1');
    expect(library.latestCapture('v1')?.id).toBe(second.id);
  });

  it('persiste por proyecto: al reabrirlo siguen las imágenes y sus metadatos', async () => {
    const storage = memoryStorage();
    const library = new ImageLibrary(storage);
    await library.open('manzana.glb');
    await library.add(image(), blob());
    await library.add(image({ kind: 'ai', prompt: 'ladrillo', provider: 'openai' }), blob());
    await library.open(null);
    expect(library.images).toEqual([]);
    await library.open('otro.glb');
    expect(library.images).toEqual([]);

    const reopened = new ImageLibrary(storage);
    await reopened.open('manzana.glb');
    expect(reopened.images.map((i) => i.kind)).toEqual(['capture', 'ai']);
    expect(reopened.images[1]?.prompt).toBe('ladrillo');
  });

  it('avisa a todos los que la muestran: una sola lista para panel y menú', async () => {
    const library = new ImageLibrary(memoryStorage());
    const panel = vi.fn();
    const menu = vi.fn();
    library.on(panel);
    library.on(menu);
    await library.open('p');
    const added = await library.add(image({ project: 'p' }), blob());
    await library.remove(added.id);
    expect(panel.mock.calls.length).toBe(menu.mock.calls.length);
    expect(panel.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(library.images).toEqual([]);
  });

  it('agrupa por vista de origen y conserva las imágenes de vistas eliminadas', async () => {
    const library = new ImageLibrary(memoryStorage());
    await library.open('p');
    const capture = await library.add(image({ project: 'p' }), blob());
    const ai = await library.add(
      image({ project: 'p', kind: 'ai', sourceImageIds: [capture.id] }),
      blob(),
    );
    await library.add(image({ project: 'p', viewId: 'gone', viewName: 'Antigua' }), blob());
    const groups = library.groups([
      { id: 'v2', name: 'Vista 2' },
      { id: 'v1', name: 'Renombrada' },
    ]);
    expect(groups.map((g) => [g.viewName, g.orphan, g.images.length])).toEqual([
      ['Vista 2', false, 0],
      ['Renombrada', false, 2],
      ['Antigua', true, 1],
    ]);
    expect(library.sourceOf(ai)?.id).toBe(capture.id);
    // Borrar el resultado de IA no borra la captura ni la vista
    await library.remove(ai.id);
    expect(library.forView('v1').map((i) => i.id)).toEqual([capture.id]);
  });

  it('un archivo que falta se notifica como ausente, sin romper la biblioteca', async () => {
    const inner = memoryStorage();
    const storage: ImageStorage = { ...inner, blob: () => Promise.resolve(null) };
    const library = new ImageLibrary(storage);
    await library.open('p');
    const added = await library.add(image({ project: 'p' }), blob());
    expect(await library.blob(added.id)).toBeNull();
    expect(library.images).toHaveLength(1);
  });

  it('si el navegador no deja guardar, sigue en memoria y lo avisa una vez', async () => {
    const failing: ImageStorage = {
      list: () => Promise.reject(new Error('sin IndexedDB')),
      blob: () => Promise.reject(new Error('sin IndexedDB')),
      put: () => Promise.reject(new Error('QuotaExceededError')),
      remove: () => Promise.resolve(),
    };
    const problem = vi.fn();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const library = new ImageLibrary(failing, problem);
    await library.open('p');
    const added = await library.add(image({ project: 'p' }), blob('dato'));
    expect(library.volatile).toBe(true);
    expect(problem).toHaveBeenCalledOnce();
    expect(await (await library.blob(added.id))?.text()).toBe('dato');
    warn.mockRestore();
  });

  it('abrir otro proyecto mientras carga el anterior no mezcla imágenes', async () => {
    const inner = memoryStorage();
    await inner.put(
      { ...image({ project: 'a' }), id: '1', createdAt: '2026-01-01', bytes: 1 },
      blob(),
    );
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    const slow: ImageStorage = {
      ...inner,
      list: async (project) => {
        if (project === 'a') await gate;
        return inner.list(project);
      },
    };
    const library = new ImageLibrary(slow);
    const first = library.open('a');
    await library.open('b');
    release();
    await first;
    expect(library.project).toBe('b');
    expect(library.images).toEqual([]);
  });
});
