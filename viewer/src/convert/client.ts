/** Cliente del servidor local de conversión (`extrude serve`). */
import type { ConversionReport } from './report';

/** Vite en desarrollo conserva el pipeline local; el build usa el origen del visor. */
export const DEFAULT_SERVER =
  import.meta.env.PROD && typeof location !== 'undefined'
    ? location.origin
    : 'http://127.0.0.1:8765';
export const CONFIG_HEADER = 'X-Extrude-Config';
export const DRAWING_EXTENSIONS = ['.dwg', '.dxf'];
export const CONFIG_EXTENSIONS = ['.yaml', '.yml'];
/**
 * La configuración viaja en Base64 en una cabecera: el servidor (http.server) no admite
 * líneas de cabecera de más de 64 kB. Se deja margen para el resto de cabeceras.
 */
export const MAX_CONFIG_HEADER_BYTES = 60 * 1024;

export interface ConvertResponse {
  job: string;
  source: string;
  config_generated: boolean;
  files: {
    glb: string | null;
    manifest: string | null;
    report_md: string;
    report_json: string;
    config: string;
  };
  report: ConversionReport;
  /** Revisión de la geometría del resultado; el modelo cargado debe llevar la misma. */
  revision?: string | null;
  /** Identidad del código del servidor que lo generó. */
  build?: string | null;
}

/**
 * Fases que publica `extrude serve` (GET /api/jobs/<id>/status) más 'upload' (envío del plano).
 * El servidor puede añadir fases nuevas: trátese como texto.
 */
export type ConvertPhase =
  | 'upload'
  | 'queued'
  | 'draft_config'
  | 'dwg_to_dxf'
  | 'read'
  | 'build'
  | 'instancing'
  | 'export'
  | 'compress'
  | 'done'
  | (string & {});

/** `fraction` en [0, 1] del total de la conversión, o null si no se puede estimar. */
export type ConvertProgress = (phase: ConvertPhase, fraction: number | null) => void;

export const DEFAULT_POLL_MS = 400;
export const DEFAULT_TIMEOUT_MS = 15 * 60_000;
export const DEFAULT_REQUEST_TIMEOUT_MS = 60_000;

export interface ConvertOptions {
  /** Tamaño de tesela en metros: el servidor genera tiles/ + manifest.json en vez de un GLB. */
  tiles?: number;
  onProgress?: ConvertProgress;
  /** Intervalo de sondeo del estado (ms). */
  pollMs?: number;
  signal?: AbortSignal;
  /** Límite total de una espera (por defecto 15 minutos). */
  timeoutMs?: number;
  /** Límite por petición, incluida la lectura de la respuesta (por defecto 60 s). */
  requestTimeoutMs?: number;
  /** Permite retomar el mismo trabajo tras interrumpir la espera, sin reenviar el plano. */
  onAccepted?: (statusPath: string) => void;
}

interface AcceptedJob {
  job: string;
  state: 'pending';
  status: string;
}

interface JobStatus {
  job: string;
  state: 'pending' | 'running' | 'done' | 'failed';
  phase: ConvertPhase;
  fraction: number;
  result?: ConvertResponse;
}

interface ServerError {
  error?: string;
  fix?: string;
}

/** En cola o generando el borrador de YAML no hay avance medible: barra indeterminada. */
const INDETERMINATE_PHASES = new Set(['queued', 'draft_config']);

function indeterminate(status: JobStatus): boolean {
  return status.state === 'pending' || INDETERMINATE_PHASES.has(status.phase);
}

function abortable<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const abort = (): void => reject(abortError(signal));
    signal.addEventListener('abort', abort, { once: true });
    promise.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
    if (signal.aborted) abort();
  });
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const finish = (): void => {
      signal.removeEventListener('abort', abort);
      resolve();
    };
    const timer = setTimeout(finish, ms);
    const abort = (): void => {
      clearTimeout(timer);
      signal.removeEventListener('abort', abort);
      reject(abortError(signal));
    };
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) abort();
  });
}

/** Qué abrir tras convertir: un GLB único o un conjunto teselado (manifest.json). */
export type ConvertedModel = { kind: 'glb'; url: string } | { kind: 'tiles'; url: string };

/** Ruta relativa del servidor del modelo a abrir; null si la respuesta no trae ninguno. */
export function convertedModel(result: ConvertResponse): ConvertedModel | null {
  if (result.files.glb) return { kind: 'glb', url: result.files.glb };
  if (result.files.manifest) return { kind: 'tiles', url: result.files.manifest };
  return null;
}

export class ConversionError extends Error {
  constructor(
    message: string,
    readonly fix: string,
  ) {
    super(message);
  }
}

export class ConversionCancelledError extends ConversionError {
  constructor() {
    super('Espera cancelada.', 'El trabajo puede seguir en el servidor. Puedes retomar la espera.');
  }
}

export class ConversionTimeoutError extends ConversionError {
  constructor() {
    super('Se agotó el tiempo de espera.', 'Comprueba el servidor y retoma la espera o reintenta.');
  }
}

export class ConversionJobEndedError extends ConversionError {}

function abortError(signal: AbortSignal): Error {
  return signal.reason instanceof Error ? signal.reason : new ConversionCancelledError();
}

export function hasExtension(name: string, extensions: string[]): boolean {
  const lower = name.toLowerCase();
  return extensions.some((ext) => lower.endsWith(ext));
}

/**
 * Qué hacer si el servidor no responde: en desarrollo (servidor en este equipo), arrancarlo;
 * si es un servidor web, no hay nada que arrancar desde el navegador.
 */
export function unreachableFix(server: string): string {
  let host = '';
  try {
    host = new URL(server).hostname;
  } catch {
    // dirección inválida: se trata como remota
  }
  return ['localhost', '127.0.0.1', '[::1]'].includes(host)
    ? 'Arráncalo con: cd pipeline && uv run extrude serve'
    : 'Recarga la página en unos minutos; si sigue sin responder, avisa a la administración del servidor.';
}

function base64Utf8(text: string): string {
  let binary = '';
  for (const byte of new TextEncoder().encode(text)) binary += String.fromCharCode(byte);
  return btoa(binary);
}

export class ConversionClient {
  constructor(
    readonly server: string = DEFAULT_SERVER,
    private readonly authorizeUpload?: () => void,
  ) {}

  url(path: string): string {
    return new URL(path, this.server).toString();
  }

  private unreachable(): ConversionError {
    return new ConversionError(
      `No se pudo contactar con el servidor de conversión (${this.server}).`,
      unreachableFix(this.server),
    );
  }

  async convert(
    file: File,
    configYaml: string | null,
    options: ConvertOptions = {},
  ): Promise<ConvertResponse> {
    this.authorizeUpload?.();
    return this.withDeadline(options, (signal) => this.upload(file, configYaml, options, signal));
  }

  /** Consulta el trabajo ya aceptado: nunca vuelve a subir el archivo. */
  async resume(statusPath: string, options: ConvertOptions = {}): Promise<ConvertResponse> {
    return this.withDeadline(options, (signal) => this.waitFor(statusPath, options, signal));
  }

  private async withDeadline(
    options: ConvertOptions,
    run: (signal: AbortSignal) => Promise<ConvertResponse>,
  ): Promise<ConvertResponse> {
    for (const value of [
      options.timeoutMs ?? DEFAULT_TIMEOUT_MS,
      options.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS,
    ]) {
      if (!Number.isFinite(value) || value <= 0 || value > 2_147_483_647) {
        throw new ConversionError('Tiempo de espera no válido.', 'Indica un tiempo finito > 0.');
      }
    }
    if (
      !Number.isFinite(options.pollMs ?? DEFAULT_POLL_MS) ||
      (options.pollMs ?? DEFAULT_POLL_MS) < 0
    ) {
      throw new ConversionError(
        'Intervalo de sondeo no válido.',
        'Indica un intervalo finito ≥ 0.',
      );
    }
    const controller = new AbortController();
    const cancel = (): void =>
      controller.abort(
        options.signal?.reason instanceof ConversionError
          ? options.signal.reason
          : new ConversionCancelledError(),
      );
    options.signal?.addEventListener('abort', cancel, { once: true });
    if (options.signal?.aborted) cancel();
    const timer = setTimeout(
      () => controller.abort(new ConversionTimeoutError()),
      options.timeoutMs ?? DEFAULT_TIMEOUT_MS,
    );
    try {
      controller.signal.throwIfAborted();
      return await abortable(run(controller.signal), controller.signal);
    } finally {
      clearTimeout(timer);
      options.signal?.removeEventListener('abort', cancel);
    }
  }

  private async request(
    path: string,
    init: RequestInit,
    signal: AbortSignal,
    options: ConvertOptions,
  ): Promise<{ response: Response; payload: unknown }> {
    signal.throwIfAborted();
    const controller = new AbortController();
    const cancel = (): void => controller.abort(signal.reason);
    signal.addEventListener('abort', cancel, { once: true });
    const timer = setTimeout(
      () => controller.abort(new ConversionTimeoutError()),
      options.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS,
    );
    try {
      return await abortable(
        (async () => {
          const response = await fetch(this.url(path), { ...init, signal: controller.signal });
          const payload: unknown = await response.json().catch(() => ({}));
          return { response, payload };
        })(),
        controller.signal,
      );
    } catch (error) {
      if (controller.signal.aborted) throw controller.signal.reason;
      if (error instanceof ConversionError) throw error;
      throw this.unreachable();
    } finally {
      clearTimeout(timer);
      signal.removeEventListener('abort', cancel);
    }
  }

  private async upload(
    file: File,
    configYaml: string | null,
    options: ConvertOptions,
    signal: AbortSignal,
  ): Promise<ConvertResponse> {
    if (!hasExtension(file.name, DRAWING_EXTENSIONS)) {
      throw new ConversionError(
        `'${file.name}' no es un plano.`,
        'El archivo de entrada debe ser .dwg o .dxf.',
      );
    }
    const headers: Record<string, string> = { 'Content-Type': 'application/octet-stream' };
    if (configYaml) {
      const encoded = base64Utf8(configYaml);
      if (encoded.length > MAX_CONFIG_HEADER_BYTES) {
        throw new ConversionError(
          `La configuración YAML es demasiado grande (${Math.round(configYaml.length / 1024)} kB): el máximo es de unos ${Math.floor((MAX_CONFIG_HEADER_BYTES * 3) / 4 / 1024)} kB.`,
          'Quita comentarios o reglas que no se usen y vuelve a soltarla.',
        );
      }
      headers[CONFIG_HEADER] = encoded;
    }
    let query = `name=${encodeURIComponent(file.name)}`;
    if (options.tiles !== undefined) {
      if (!Number.isFinite(options.tiles) || !(options.tiles > 0)) {
        throw new ConversionError('Tamaño de tesela no válido.', 'Indica un tamaño > 0 m.');
      }
      query += `&tiles=${encodeURIComponent(String(options.tiles))}`;
    }
    // async=1: el servidor responde 202 al instante y se sondea el estado por fases. Un
    // servidor anterior ignora el parámetro y responde 200 con el resultado (también vale).
    query += '&async=1';
    options.onProgress?.('upload', null);
    const { response, payload: raw } = await this.request(
      `/api/convert?${query}`,
      {
        method: 'POST',
        headers,
        body: file,
      },
      signal,
      options,
    );
    const payload = (raw ?? {}) as Partial<ConvertResponse> & Partial<AcceptedJob> & ServerError;
    if (!response.ok) {
      throw new ConversionError(payload.error ?? `HTTP ${response.status}`, payload.fix ?? '');
    }
    if (response.status === 202 && payload.status) {
      options.onAccepted?.(payload.status);
      return this.waitFor(payload.status, options, signal);
    }
    if (response.status === 202)
      throw new ConversionError(
        'Respuesta de conversión incompleta.',
        'Actualiza extrude serve y reintenta.',
      );
    return payload as ConvertResponse;
  }

  /** Sondea GET /api/jobs/<id>/status hasta que el trabajo termina o falla. */
  private async waitFor(
    statusPath: string,
    options: ConvertOptions,
    signal: AbortSignal,
  ): Promise<ConvertResponse> {
    const interval = options.pollMs ?? DEFAULT_POLL_MS;
    let lastPhase = '';
    let lastFraction = -1;
    for (;;) {
      const { response, payload } = await this.request(statusPath, {}, signal, options);
      const status = (payload ?? {}) as JobStatus & ServerError;
      if (!response.ok) {
        if (response.status === 404) {
          throw new ConversionJobEndedError(
            'El trabajo ya no está disponible.',
            'Reenvía el plano para convertirlo de nuevo.',
          );
        }
        throw new ConversionError(status.error ?? `HTTP ${response.status}`, status.fix ?? '');
      }
      if (status.state === 'done' && status.result) return status.result;
      if (status.state === 'failed') {
        throw new ConversionJobEndedError(status.error ?? 'La conversión falló.', status.fix ?? '');
      }
      if (status.state !== 'pending' && status.state !== 'running') {
        throw new ConversionError(
          'El servidor devolvió un estado incompleto.',
          'Comprueba extrude serve y retoma la espera.',
        );
      }
      if (status.phase !== lastPhase || status.fraction !== lastFraction) {
        lastPhase = status.phase;
        lastFraction = status.fraction;
        options.onProgress?.(status.phase, indeterminate(status) ? null : status.fraction);
      }
      await sleep(interval, signal);
    }
  }
}
