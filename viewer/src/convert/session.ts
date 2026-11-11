import {
  ConversionClient,
  ConversionJobEndedError,
  type ConvertOptions,
  type ConvertResponse,
} from './client';

/** Conserva el trabajo aceptado y el resultado para que un reintento no duplique conversiones. */
export class ConversionSession {
  private statusPath: string | null = null;
  private result: ConvertResponse | null = null;

  constructor(
    readonly file: File,
    private readonly config: string | null,
    private readonly tiles?: number,
  ) {}

  get retryLabel(): string {
    if (this.result) return 'Reabrir resultado';
    return this.statusPath ? 'Retomar espera' : 'Reintentar envío';
  }

  /** El servidor ya eliminó los archivos: el siguiente intento necesita una conversión nueva. */
  discardExpiredResult(): void {
    this.result = null;
    this.statusPath = null;
  }

  get recoveryHint(): string {
    if (this.result) return 'La conversión terminó. Se volverá a cargar el mismo resultado.';
    if (this.statusPath)
      return 'El trabajo puede seguir en el servidor. Retomarlo no reenvía el plano.';
    return 'No hay un trabajo recuperable confirmado. Reintentar volverá a enviar el plano.';
  }

  async run(client: ConversionClient, options: ConvertOptions): Promise<ConvertResponse> {
    options.signal?.throwIfAborted();
    if (this.result) return this.result;
    try {
      this.result = this.statusPath
        ? await client.resume(this.statusPath, options)
        : await client.convert(this.file, this.config, {
            ...options,
            ...(this.tiles !== undefined ? { tiles: this.tiles } : {}),
            onAccepted: (path) => {
              this.statusPath = path;
              options.onAccepted?.(path);
            },
          });
      return this.result;
    } catch (error) {
      if (error instanceof ConversionJobEndedError) this.statusPath = null;
      throw error;
    }
  }
}
