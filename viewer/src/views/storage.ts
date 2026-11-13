/**
 * Persistencia local en IndexedDB, por modelo: las vistas (con sus miniaturas) y la biblioteca
 * de imágenes (capturas del visor y resultados de IA, guardados como Blob: una imagen 4K no
 * cabe ni debe ir en localStorage). Si IndexedDB no está disponible (modo privado, permisos) se
 * avisa y todo vive solo en memoria: exporta views.json y descarga las imágenes para
 * conservarlas.
 */
import type { SceneView } from './viewState';

const DB_NAME = 'extrude-viewer';
const DB_VERSION = 2;
const VIEWS = 'views';
export const IMAGES = 'images';
export const IMAGES_BY_PROJECT = 'project';

interface Record_ {
  model: string;
  views: SceneView[];
  updatedAt: string;
}

export function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      reject(new Error('IndexedDB no disponible'));
      return;
    }
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(VIEWS)) db.createObjectStore(VIEWS, { keyPath: 'model' });
      if (!db.objectStoreNames.contains(IMAGES)) {
        db.createObjectStore(IMAGES, { keyPath: 'id' }).createIndex(IMAGES_BY_PROJECT, 'project');
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error('No se pudo abrir IndexedDB'));
    request.onblocked = () => reject(new Error('Cierra otras pestañas de Extrude y recarga.'));
  });
}

/** Una transacción sobre un almacén; resuelve con el resultado de `body` al completarse. */
export function transact<T>(
  store: string,
  mode: IDBTransactionMode,
  body: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  return openDatabase().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const tx = db.transaction(store, mode);
        const request = body(tx.objectStore(store));
        tx.oncomplete = () => {
          db.close();
          resolve(request.result);
        };
        const fail = (): void => {
          db.close();
          reject(tx.error ?? request.error ?? new Error('Error de IndexedDB'));
        };
        tx.onerror = fail;
        tx.onabort = fail;
      }),
  );
}

export async function loadViews(model: string): Promise<SceneView[]> {
  const record = await transact<Record_ | undefined>(
    VIEWS,
    'readonly',
    (store) => store.get(model) as IDBRequest<Record_ | undefined>,
  );
  return record?.views ?? [];
}

export async function saveViews(model: string, views: SceneView[]): Promise<void> {
  const record: Record_ = { model, views, updatedAt: new Date().toISOString() };
  await transact(VIEWS, 'readwrite', (store) => store.put(record));
}
