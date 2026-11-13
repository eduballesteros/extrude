/**
 * Cliente de la generación de imágenes (`/api/images/*`). Las claves y las llamadas al
 * proveedor viven en el servidor; aquí solo se envían las capturas elegidas y el prompt.
 *
 * Una generación es un trabajo asíncrono: el servidor responde al instante y se consulta su
 * estado. `requestId` identifica la solicitud: reenviarla (doble clic, reintento tras un corte
 * de red) devuelve el mismo trabajo en vez de lanzar otra llamada de pago.
 */

export type Target = 'standard' | 'hd' | '4k';

export const TARGET_LABEL: Record<Target, string> = {
  standard: 'Estándar del modelo',
  hd: 'HD',
  '4k': '4K',
};

export interface ImageProviderInfo {
  id: string;
  name: string;
  models: string[];
  simulated: boolean;
  available: boolean;
  reason: string | null;
}

export interface ImageProviders {
  providers: ImageProviderInfo[];
  limits: { maxPromptChars: number; maxReferences: number; maxReferenceBytes: number };
  fidelityNote: string;
  promptAssist: { available: boolean; reason: string | null; model: string };
}

export interface SizePlan {
  target: Target;
  /** Lo que se pide al proveedor, legible. */
  label: string;
  width: number | null;
  height: number | null;
  /** El proveedor genera esa resolución de forma nativa. */
  native: boolean;
  note: string;
}

export interface GenerationResult {
  mime: string;
  width: number;
  height: number;
  bytes: number;
  model: string;
  file: string;
  simulated: boolean;
  matchesRequest: boolean;
}

export interface GenerationJob {
  id: string;
  state: 'pending' | 'running' | 'done' | 'failed' | 'cancelled';
  provider: string;
  model: string;
  requested: SizePlan;
  status: string;
  result?: GenerationResult;
  error?: string;
  fix?: string;
  kind?: string;
}

export interface GenerationRequest {
  provider: string;
  model: string;
  prompt: string;
  target: Target;
  /** Capturas de referencia en Base64 (PNG/JPEG/WebP). */
  images: { data: string }[];
  requestId: string;
}

export class GenerationError extends Error {
  constructor(
    message: string,
    readonly fix = '',
    readonly kind = 'error',
    /** El servidor dio una respuesta definitiva: un reintento es una solicitud nueva. */
    readonly definitive = true,
  ) {
    super(message);
  }
}

export class GenerationCancelled extends GenerationError {
  constructor() {
    super(
      'Generación cancelada.',
      'El proveedor puede haber terminado (y facturado) la imagen: no se ha guardado.',
      'cancelled',
    );
  }
}

const POLL_MS = 1000;

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

export class ImageGenClient {
  constructor(private readonly server: string) {}

  private url(path: string): string {
    return new URL(path, this.server).toString();
  }

  private async call<T>(path: string, body?: unknown): Promise<T> {
    let response: Response;
    try {
      response = await fetch(
        this.url(path),
        body === undefined
          ? {}
          : {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(body),
            },
      );
    } catch {
      throw new GenerationError(
        'No se pudo contactar con el servidor de Extrude.',
        'Comprueba la conexión y reintenta: no se duplicará la solicitud.',
        'network',
        false,
      );
    }
    const payload = (await response.json().catch(() => ({}))) as T & {
      error?: string;
      fix?: string;
      kind?: string;
    };
    if (!response.ok) {
      throw new GenerationError(
        payload.error ?? `Error ${response.status}`,
        payload.fix ?? '',
        payload.kind ?? 'error',
      );
    }
    return payload;
  }

  providers(): Promise<ImageProviders> {
    return this.call<ImageProviders>('/api/images/providers');
  }

  /** Qué resolución se pediría para cada objetivo con esa vista (sin coste). */
  async plan(provider: string, model: string, width: number, height: number): Promise<SizePlan[]> {
    const data = await this.call<{ plans: SizePlan[] }>('/api/images/plan', {
      provider,
      model,
      width,
      height,
    });
    return data.plans;
  }

  /** Claude propone una redacción del prompt a partir de las capturas. No genera imágenes. */
  async suggest(prompt: string, images: { data: string }[]): Promise<string> {
    const data = await this.call<{ suggestion: string }>('/api/images/suggest', { prompt, images });
    return data.suggestion;
  }

  /**
   * Lanza la generación y espera el archivo. `signal` cancela: se deja de esperar y el servidor
   * descarta el resultado (el proveedor puede terminar y facturar igualmente).
   */
  async generate(
    request: GenerationRequest,
    options: { signal?: AbortSignal; onJob?: (job: GenerationJob) => void } = {},
  ): Promise<{ job: GenerationJob; blob: Blob }> {
    const { signal, onJob } = options;
    let job = await this.call<GenerationJob>('/api/images/generate', request);
    onJob?.(job);
    while (job.state === 'pending' || job.state === 'running') {
      if (signal?.aborted) {
        await this.call(`/api/images/jobs/${job.id}/cancel`, {}).catch(() => undefined);
        throw new GenerationCancelled();
      }
      await sleep(POLL_MS, signal);
      if (signal?.aborted) continue;
      try {
        job = await this.call<GenerationJob>(job.status);
      } catch (error) {
        // Un corte de red al consultar no pierde el trabajo: se sigue consultando
        if (error instanceof GenerationError && !error.definitive) continue;
        throw error;
      }
      onJob?.(job);
    }
    if (job.state === 'cancelled') throw new GenerationCancelled();
    if (job.state === 'failed' || !job.result) {
      throw new GenerationError(
        job.error ?? 'La generación falló.',
        job.fix ?? '',
        job.kind ?? 'failed',
      );
    }
    let response: Response;
    try {
      response = await fetch(this.url(job.result.file));
    } catch {
      throw new GenerationError(
        'La imagen se generó, pero no se pudo descargar del servidor.',
        'Reintenta: se recuperará la misma imagen sin volver a generarla.',
        'network',
        false,
      );
    }
    if (!response.ok) {
      throw new GenerationError('La imagen ya no está en el servidor.', 'Vuelve a generarla.');
    }
    return { job, blob: await response.blob() };
  }
}
