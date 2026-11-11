import { ConversionError, DEFAULT_SERVER } from './client';

function serverOrigin(value: string): string {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) {
    throw new Error('Destino de conversión no válido');
  }
  return url.origin;
}

/** La confianza proviene de la configuración, nunca de un parámetro de un enlace. */
export class ConversionDestination {
  readonly server: string;
  readonly local: boolean;
  readonly requiresAcceptance: boolean;
  readonly error: string | null;
  private accepted = false;

  constructor(queryServer: string | null, configuredServer?: string) {
    try {
      this.server = serverOrigin(queryServer ?? configuredServer ?? DEFAULT_SERVER);
      this.local = ['localhost', '127.0.0.1', '[::1]'].includes(new URL(this.server).hostname);
      let trustedOrigin: string | null = null;
      try {
        trustedOrigin = serverOrigin(configuredServer ?? DEFAULT_SERVER);
      } catch {
        // Una configuración inválida no concede confianza a una URL distinta.
      }
      this.requiresAcceptance =
        queryServer !== null && !this.local && this.server !== trustedOrigin;
      this.error = null;
    } catch {
      this.server = DEFAULT_SERVER;
      this.local = false;
      this.requiresAcceptance = false;
      this.error = 'Destino de conversión no válido. Usa una URL HTTP(S) sin credenciales.';
    }
  }

  get description(): string {
    if (this.error) return this.error;
    return this.local
      ? 'Los planos se convierten en tu equipo con extrude serve.'
      : 'Los planos y su configuración se enviarán a este servidor remoto.';
  }

  setAccepted(accepted: boolean): void {
    this.accepted = accepted;
  }

  assertUploadAllowed(): void {
    if (this.error) throw new ConversionError(this.error, 'Revisa ?server o la configuración.');
    if (this.requiresAcceptance && !this.accepted) {
      throw new ConversionError(
        `El enlace ha indicado un servidor remoto: ${this.server}.`,
        'Acepta explícitamente el destino antes de elegir o soltar el plano.',
      );
    }
  }
}
