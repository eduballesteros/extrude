/**
 * Persistencia local de vistas por modelo en IndexedDB (las miniaturas no caben bien en
 * localStorage). Si IndexedDB no está disponible (modo privado, permisos) se avisa y las vistas
 * viven solo en memoria: exporta views.json para conservarlas.
 */
import type { SceneView } from './viewState';

const DB_NAME = 'extrude-viewer';
const DB_VERSION = 1;
const STORE = 'views';

interface Record_ {
  model: string;
  views: SceneView[];
  updatedAt: string;
}

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      reject(new Error('IndexedDB no disponible'));
      return;
    }
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE, { keyPath: 'model' });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error('No se pudo abrir IndexedDB'));
  });
}

function run<T>(
  mode: IDBTransactionMode,
  body: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  return open().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const tx = db.transaction(STORE, mode);
        const request = body(tx.objectStore(STORE));
        tx.oncomplete = () => {
          db.close();
          resolve(request.result);
        };
        tx.onerror = () => {
          db.close();
          reject(tx.error ?? new Error('Error de IndexedDB'));
        };
      }),
  );
}

export async function loadViews(model: string): Promise<SceneView[]> {
  const record = await run<Record_ | undefined>(
    'readonly',
    (store) => store.get(model) as IDBRequest<Record_ | undefined>,
  );
  return record?.views ?? [];
}

export async function saveViews(model: string, views: SceneView[]): Promise<void> {
  const record: Record_ = { model, views, updatedAt: new Date().toISOString() };
  await run('readwrite', (store) => store.put(record));
}
