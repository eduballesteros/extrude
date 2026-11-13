/**
 * Biblioteca única de imágenes del proyecto: capturas del visor y resultados generados por IA.
 *
 * `ImageLibrary` es la única fuente de verdad: el panel de vistas y la biblioteca del menú
 * principal pintan desde ella y se enteran de los cambios con `on()`. Los archivos (Blob) y sus
 * metadatos se guardan en IndexedDB por proyecto (nombre del modelo, igual que las vistas), así
 * que siguen ahí al recargar la página o reabrir el proyecto en este navegador.
 *
 * Reglas:
 * - Nada se sobrescribe: cada captura o generación es un registro nuevo con su fecha, su vista
 *   de origen y la revisión del modelo. Volver a capturar una vista conserva la anterior.
 * - Borrar una vista no borra sus imágenes (quedan como «vista eliminada»); borrar una imagen
 *   no toca la vista.
 * - Si el archivo de un registro falta (almacenamiento vaciado por el navegador), el registro
 *   se muestra como «archivo no disponible» y solo se puede quitar.
 * - Las imágenes viven en este navegador: cerrar el proyecto no las borra; «Vaciar biblioteca»
 *   sí. No se envían al servidor salvo como referencia de una generación que el usuario lanza.
 */
import { IMAGES, IMAGES_BY_PROJECT, transact } from '../views/storage';

export type ImageKind = 'capture' | 'ai';
export type CapturePreset = 'HD' | '4K';

export const PRESET_BOX: Record<CapturePreset, { width: number; height: number }> = {
  HD: { width: 1920, height: 1080 },
  '4K': { width: 3840, height: 2160 },
};

export const KIND_LABEL: Record<ImageKind, string> = {
  capture: 'Captura del visor',
  ai: 'Generada por IA',
};

/** Tipo que se muestra: una imagen del proveedor simulado nunca se presenta como IA. */
export function kindLabel(image: { kind: ImageKind; simulated?: boolean }): string {
  return image.kind === 'ai' && image.simulated
    ? 'Imagen de prueba (sin IA)'
    : KIND_LABEL[image.kind];
}

export interface LibraryImage {
  id: string;
  project: string;
  viewId: string;
  /** Nombre de la vista al crear la imagen (la vista puede renombrarse o borrarse después). */
  viewName: string;
  kind: ImageKind;
  createdAt: string;
  /** Dimensiones reales del archivo (leídas de la imagen, no las pedidas). */
  width: number;
  height: number;
  mime: string;
  bytes: number;
  filename: string;
  thumbnail: string;
  /** Captura: resolución elegida y revisión del modelo en ese momento. */
  preset?: CapturePreset;
  modelRevision?: string | null;
  /** IA: procedencia completa. */
  prompt?: string;
  provider?: string;
  providerName?: string;
  model?: string;
  requested?: { label: string; width: number | null; height: number | null };
  /** Capturas enviadas como referencia. */
  sourceImageIds?: string[];
  /** Imagen de prueba del proveedor simulado (no es IA). */
  simulated?: boolean;
  /** El archivo recibido alcanza la resolución pedida. */
  matchesRequest?: boolean;
  /** Ampliación por interpolación de otra imagen (no es resolución nativa). */
  upscaledFrom?: string;
}

interface StoredImage extends LibraryImage {
  blob: Blob;
}

export type NewImage = Omit<LibraryImage, 'id' | 'createdAt' | 'bytes'>;

/** Almacenamiento de la biblioteca (IndexedDB en el visor; en memoria en las pruebas). */
export interface ImageStorage {
  list(project: string): Promise<LibraryImage[]>;
  blob(id: string): Promise<Blob | null>;
  put(image: LibraryImage, blob: Blob): Promise<void>;
  remove(id: string): Promise<void>;
}

function withoutBlob(record: StoredImage): LibraryImage {
  const meta: LibraryImage & { blob?: Blob } = { ...record };
  delete meta.blob;
  return meta;
}

export const indexedDbStorage: ImageStorage = {
  async list(project) {
    const records = await transact<StoredImage[]>(
      IMAGES,
      'readonly',
      (store) => store.index(IMAGES_BY_PROJECT).getAll(project) as IDBRequest<StoredImage[]>,
    );
    return records.map(withoutBlob);
  },
  async blob(id) {
    const record = await transact<StoredImage | undefined>(
      IMAGES,
      'readonly',
      (store) => store.get(id) as IDBRequest<StoredImage | undefined>,
    );
    return record?.blob instanceof Blob && record.blob.size > 0 ? record.blob : null;
  },
  async put(image, blob) {
    await transact(IMAGES, 'readwrite', (store) => store.put({ ...image, blob }));
  },
  async remove(id) {
    await transact(IMAGES, 'readwrite', (store) => store.delete(id));
  },
};

/** Almacenamiento en memoria: pruebas y navegadores sin IndexedDB (dura lo que la página). */
export function memoryStorage(): ImageStorage {
  const records = new Map<string, StoredImage>();
  return {
    list: (project) =>
      Promise.resolve([...records.values()].filter((r) => r.project === project).map(withoutBlob)),
    blob: (id) => Promise.resolve(records.get(id)?.blob ?? null),
    put: (image, blob) => {
      records.set(image.id, { ...image, blob });
      return Promise.resolve();
    },
    remove: (id) => {
      records.delete(id);
      return Promise.resolve();
    },
  };
}

// --- Dimensiones y nombres ----------------------------------------------------------------------

/**
 * Tamaño final de una captura HD o 4K: el mayor de la proporción de la vista que cabe en
 * 1920×1080 o 3840×2160 (una vista 16:9 los llena; una 4:3 en HD da 1440×1080).
 */
export function captureSize(
  viewWidth: number,
  viewHeight: number,
  preset: CapturePreset,
): { width: number; height: number } {
  const box = PRESET_BOX[preset];
  const aspect = viewWidth / viewHeight;
  const even = (value: number): number => Math.max(2, Math.round(value / 2) * 2);
  return aspect >= box.width / box.height
    ? { width: box.width, height: even(box.width / aspect) }
    : { width: even(box.height * aspect), height: box.height };
}

function slug(text: string): string {
  const clean = text
    .normalize('NFKC')
    .replace(/\.(glb|gltf|dwg|dxf)$/i, '')
    .replace(/[^\p{L}\p{N}]+/gu, '-')
    .replace(/^-+|-+$/g, '');
  return clean.slice(0, 40) || 'sin-nombre';
}

function stamp(date: Date): string {
  const p = (n: number): string => String(n).padStart(2, '0');
  return `${date.getFullYear()}${p(date.getMonth() + 1)}${p(date.getDate())}-${p(date.getHours())}${p(date.getMinutes())}${p(date.getSeconds())}`;
}

const EXTENSION: Record<string, string> = {
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/webp': 'webp',
};

/**
 * proyecto_vista_tipo_ANCHOxALTO_fecha.ext — el tipo dice qué es: `captura`, `ia`,
 * `prueba` (proveedor simulado) y, si es una ampliación, con el sufijo `-ampliada`. La
 * fecha evita nombres repetidos.
 */
export function imageFilename(options: {
  project: string;
  view: string;
  kind: ImageKind;
  width: number;
  height: number;
  mime: string;
  date: Date;
  upscaled?: boolean;
  simulated?: boolean;
}): string {
  const type =
    options.kind === 'capture'
      ? 'captura'
      : `${options.simulated ? 'prueba' : 'ia'}${options.upscaled ? '-ampliada' : ''}`;
  const ext = EXTENSION[options.mime] ?? 'png';
  return `${slug(options.project)}_${slug(options.view)}_${type}_${options.width}x${options.height}_${stamp(options.date)}.${ext}`;
}

/** Etiqueta de resolución a partir de las dimensiones reales. */
export function resolutionLabel(width: number, height: number): string {
  const long = Math.max(width, height);
  const short = Math.min(width, height);
  if (long >= 3840 || short >= 2160) return '4K';
  if (long >= 1920 || short >= 1080) return 'HD';
  return '';
}

type Listener = () => void;

export interface ViewGroup {
  viewId: string;
  viewName: string;
  /** La vista ya no existe en el proyecto. */
  orphan: boolean;
  images: LibraryImage[];
}

export class ImageLibrary {
  images: LibraryImage[] = [];
  project: string | null = null;
  /** El almacenamiento persistente falló: las imágenes solo duran lo que la página. */
  volatile = false;
  private storage: ImageStorage;
  private token = 0;
  private readonly listeners = new Set<Listener>();
  private readonly fallback = memoryStorage();

  constructor(
    storage: ImageStorage = indexedDbStorage,
    private readonly onProblem: (message: string) => void = () => {},
  ) {
    this.storage = storage;
  }

  on(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private changed(): void {
    for (const listener of this.listeners) listener();
  }

  /** Abre la biblioteca de un proyecto (o ninguna). Las cargas anteriores pendientes se anulan. */
  async open(project: string | null): Promise<void> {
    if (project === this.project) return;
    const token = ++this.token;
    this.project = project;
    this.images = [];
    this.changed();
    if (!project) return;
    let images: LibraryImage[];
    try {
      images = await this.storage.list(project);
    } catch (error) {
      console.warn(error);
      if (token !== this.token) return;
      this.useFallback();
      images = await this.storage.list(project);
    }
    if (token !== this.token) return;
    const added = this.images; // creadas mientras llegaba la lista guardada
    this.images = [...images, ...added].sort((a, b) => a.createdAt.localeCompare(b.createdAt));
    this.changed();
  }

  private useFallback(): void {
    if (this.volatile) return;
    this.volatile = true;
    this.storage = this.fallback;
    this.onProblem(
      'El navegador no deja guardar imágenes (modo privado o sin espacio): la biblioteca solo durará hasta cerrar la página. Descarga lo que quieras conservar.',
    );
  }

  /** Añade una imagen nueva. Nunca sustituye a otra. */
  async add(image: NewImage, blob: Blob, date = new Date()): Promise<LibraryImage> {
    const record: LibraryImage = {
      ...image,
      id: crypto.randomUUID(),
      createdAt: date.toISOString(),
      bytes: blob.size,
    };
    try {
      await this.storage.put(record, blob);
    } catch (error) {
      console.warn(error);
      if (this.volatile) throw error;
      this.useFallback();
      await this.storage.put(record, blob);
    }
    if (record.project === this.project) {
      this.images = [...this.images, record];
      this.changed();
    }
    return record;
  }

  /** Archivo de una imagen; null si falta (se muestra como «archivo no disponible»). */
  async blob(id: string): Promise<Blob | null> {
    try {
      return await this.storage.blob(id);
    } catch (error) {
      console.warn(error);
      return null;
    }
  }

  async remove(id: string): Promise<void> {
    await this.storage.remove(id);
    this.images = this.images.filter((image) => image.id !== id);
    this.changed();
  }

  /** Vacía la biblioteca del proyecto abierto (capturas y resultados de IA). */
  async removeAll(): Promise<number> {
    const ids = this.images.map((image) => image.id);
    for (const id of ids) await this.storage.remove(id);
    this.images = [];
    this.changed();
    return ids.length;
  }

  get(id: string): LibraryImage | undefined {
    return this.images.find((image) => image.id === id);
  }

  forView(viewId: string): LibraryImage[] {
    return this.images.filter((image) => image.viewId === viewId);
  }

  /** Captura más reciente de una vista (referencia por defecto para generar). */
  latestCapture(viewId: string): LibraryImage | undefined {
    return this.forView(viewId)
      .filter((image) => image.kind === 'capture')
      .at(-1);
  }

  /** Captura original de la que salió un resultado de IA (para comparar). */
  sourceOf(image: LibraryImage): LibraryImage | undefined {
    const origin = image.upscaledFrom ? this.get(image.upscaledFrom) : image;
    const id = origin?.sourceImageIds?.[0];
    return (id ? this.get(id) : undefined) ?? this.latestCapture(image.viewId);
  }

  /**
   * Imágenes agrupadas por vista de origen, en el orden de las vistas del proyecto; al final,
   * las de vistas que ya no existen.
   */
  groups(views: readonly { id: string; name: string }[]): ViewGroup[] {
    const groups: ViewGroup[] = views.map((view) => ({
      viewId: view.id,
      viewName: view.name,
      orphan: false,
      images: this.forView(view.id),
    }));
    const known = new Set(views.map((view) => view.id));
    const orphans = new Map<string, ViewGroup>();
    for (const image of this.images) {
      if (known.has(image.viewId)) continue;
      let group = orphans.get(image.viewId);
      if (!group) {
        group = { viewId: image.viewId, viewName: image.viewName, orphan: true, images: [] };
        orphans.set(image.viewId, group);
      }
      group.images.push(image);
    }
    return [...groups, ...orphans.values()];
  }
}
