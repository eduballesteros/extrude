/**
 * Sesión del modo compartido (`extrude serve --shared`).
 *
 * El token vive en sessionStorage, separado por origen del servidor (se pierde al cerrar la
 * pestaña), y se añade como
 * `Authorization: Bearer …` a TODA petición dirigida al servidor de Extrude: las del visor y las
 * de three.js (GLTFLoader usa fetch). Las descargas se hacen con fetch + Blob, porque un enlace
 * <a download> no puede llevar cabeceras. En modo local no cambia nada.
 */

const KEY = 'extrude.session';

export interface SessionInfo {
  mode: 'local' | 'shared';
  user: string | null;
}

let token: string | null = null;
let serverOrigin: string | null = null;
const listeners = new Set<() => void>();

function sessionKey(origin: string): string {
  return `${KEY}:${origin}`;
}

function readStored(origin: string): string | null {
  try {
    // Los tokens antiguos no tienen emisor: no se pueden migrar de forma segura.
    globalThis.sessionStorage.removeItem(KEY);
    return globalThis.sessionStorage.getItem(sessionKey(origin));
  } catch {
    return null;
  }
}

export function sessionToken(): string | null {
  return token;
}

export function setSessionToken(value: string | null): void {
  if (!serverOrigin) return;
  token = value;
  try {
    if (value) globalThis.sessionStorage.setItem(sessionKey(serverOrigin), value);
    else globalThis.sessionStorage.removeItem(sessionKey(serverOrigin));
  } catch {
    /* almacenamiento no disponible: la sesión dura lo que la página */
  }
}

/** Avisa cuando el servidor pide iniciar sesión (401 auth_required). */
export function onAuthRequired(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function targetsServer(input: RequestInfo | URL): boolean {
  if (!serverOrigin) return false;
  const raw = input instanceof Request ? input.url : String(input);
  try {
    return new URL(raw, pageUrl()).origin === serverOrigin;
  } catch {
    return false;
  }
}

/** Cabeceras con la sesión para una petición al servidor (para quien no use fetch global). */
export function withSession(init: RequestInit = {}): RequestInit {
  if (!token) return init;
  const headers = new Headers(init.headers);
  headers.set('Authorization', `Bearer ${token}`);
  return { ...init, headers };
}

/** Instala la cabecera de sesión en `fetch` para el origen del servidor (una vez). */
export function installSessionFetch(server: string): void {
  const origin = new URL(server, pageUrl()).origin;
  if (origin !== serverOrigin) {
    serverOrigin = origin;
    token = readStored(origin);
  }
  const current = globalThis.fetch;
  if ((current as unknown as { __extrude?: boolean }).__extrude) return;
  const original = current.bind(globalThis);
  const patched = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    if (!targetsServer(input)) return original(input, init);
    const requestOrigin = serverOrigin;
    const requestToken = token;
    const requestInit: RequestInit = { ...init };
    const headers = init?.headers ?? (input instanceof Request ? input.headers : undefined);
    if (headers) requestInit.headers = headers;
    const response = await original(input, withSession(requestInit));
    if (response.status === 401) {
      const kind = await response
        .clone()
        .json()
        .then((d: { kind?: string }) => d.kind)
        .catch(() => undefined);
      if (kind === 'auth_required' && serverOrigin === requestOrigin && token === requestToken) {
        setSessionToken(null);
        listeners.forEach((l) => l());
      }
    }
    return response;
  };
  (patched as unknown as { __extrude: boolean }).__extrude = true;
  globalThis.fetch = patched;
}

export async function sessionInfo(server: string): Promise<SessionInfo> {
  const response = await fetch(new URL('/api/auth/me', server));
  if (!response.ok) return { mode: 'local', user: null };
  return (await response.json()) as SessionInfo;
}

export async function login(server: string, user: string, password: string): Promise<string> {
  const origin = new URL(server, pageUrl()).origin;
  const response = await fetch(new URL('/api/auth/login', server), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user, password }),
  });
  const data = (await response.json().catch(() => ({}))) as { token?: string; error?: string };
  if (!response.ok || !data.token) throw new Error(data.error ?? 'No se pudo iniciar sesión.');
  if (origin !== serverOrigin) throw new Error('El servidor cambió durante el inicio de sesión.');
  setSessionToken(data.token);
  return user;
}

export async function logout(server: string): Promise<void> {
  const origin = new URL(server, pageUrl()).origin;
  const previous = token;
  await fetch(new URL('/api/auth/logout', server), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  }).catch(() => undefined);
  if (origin === serverOrigin && token === previous) setSessionToken(null);
}

/** Descarga con la sesión: fetch + Blob (un enlace no puede llevar la cabecera). */
export async function downloadWithSession(href: string, filename: string): Promise<void> {
  const response = await fetch(href);
  if (!response.ok) throw new Error(`No se pudo descargar ${filename} (${response.status}).`);
  const url = URL.createObjectURL(await response.blob());
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

function pageUrl(): string {
  return typeof location === 'undefined' ? 'http://localhost/' : location.href;
}
