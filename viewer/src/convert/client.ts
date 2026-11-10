/** Cliente del servidor local de conversión (`extrude serve`). */

export const DEFAULT_SERVER = 'http://127.0.0.1:8765';
export const CONFIG_HEADER = 'X-Extrude-Config';
export const DRAWING_EXTENSIONS = ['.dwg', '.dxf'];
export const CONFIG_EXTENSIONS = ['.yaml', '.yml'];

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
  report: {
    warnings: string[];
    output: { triangles: number; total_size_bytes: number };
    timings_s: Record<string, number>;
  };
}

export class ConversionError extends Error {
  constructor(
    message: string,
    readonly fix: string,
  ) {
    super(message);
  }
}

export function hasExtension(name: string, extensions: string[]): boolean {
  const lower = name.toLowerCase();
  return extensions.some((ext) => lower.endsWith(ext));
}

function base64Utf8(text: string): string {
  let binary = '';
  for (const byte of new TextEncoder().encode(text)) binary += String.fromCharCode(byte);
  return btoa(binary);
}

export class ConversionClient {
  constructor(readonly server: string = DEFAULT_SERVER) {}

  url(path: string): string {
    return new URL(path, this.server).toString();
  }

  private unreachable(): ConversionError {
    return new ConversionError(
      `No se pudo contactar con el servidor de conversión (${this.server}).`,
      'Arráncalo con: cd pipeline && uv run extrude serve',
    );
  }

  async convert(file: File, configYaml: string | null): Promise<ConvertResponse> {
    if (!hasExtension(file.name, DRAWING_EXTENSIONS)) {
      throw new ConversionError(
        `'${file.name}' no es un plano.`,
        'El archivo de entrada debe ser .dwg o .dxf.',
      );
    }
    const headers: Record<string, string> = { 'Content-Type': 'application/octet-stream' };
    if (configYaml) headers[CONFIG_HEADER] = base64Utf8(configYaml);
    let response: Response;
    try {
      response = await fetch(this.url(`/api/convert?name=${encodeURIComponent(file.name)}`), {
        method: 'POST',
        headers,
        body: file,
      });
    } catch {
      throw this.unreachable();
    }
    const payload = (await response.json().catch(() => ({}))) as Partial<ConvertResponse> & {
      error?: string;
      fix?: string;
    };
    if (!response.ok) {
      throw new ConversionError(payload.error ?? `HTTP ${response.status}`, payload.fix ?? '');
    }
    return payload as ConvertResponse;
  }
}
