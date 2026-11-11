/** Materialidad en `extrude serve`: biblioteca, registro del GLB base y aplicación de planes. */
import { ConversionError } from '../convert/client';
import type { MaterialPlan } from './plan';

export interface LibraryMaterial {
  id: string;
  version: string;
  name: string;
  family: string;
  description: string;
  color: string;
  roughness: number;
  metalness: number;
  opacity: number;
  tileSizeM: [number, number];
  /** Ruta del servidor de la textura (PNG), o null si el material no lleva textura. */
  texture: string | null;
}

export interface Library {
  version: string;
  materials: LibraryMaterial[];
}

export interface PlanIssue {
  code: string;
  message: string;
  assignment: number | null;
}

export interface ApplyResult {
  preview: boolean;
  materialRevision: string;
  geometryRevision: string;
  url: string;
  planUrl?: string;
  stats: { materials: number; textures: number; draw_calls: number; assigned_triangles: number };
}

/** El servidor rechazó el plan: `issues` explica cada problema (RF-07). */
export class PlanRejectedError extends ConversionError {
  constructor(
    message: string,
    fix: string,
    readonly issues: PlanIssue[],
  ) {
    super(message, fix);
  }
}

interface ErrorPayload {
  error?: string;
  fix?: string;
  issues?: PlanIssue[];
}

export class MaterialsClient {
  constructor(readonly server: string) {}

  url(path: string): string {
    return new URL(path, this.server).toString();
  }

  private async call<T>(path: string, init?: RequestInit): Promise<T> {
    let response: Response;
    try {
      response = await fetch(this.url(path), init);
    } catch {
      throw new ConversionError(
        `No se pudo contactar con el servidor (${this.server}).`,
        'Arráncalo con: cd pipeline && uv run extrude serve',
      );
    }
    const payload = (await response.json().catch(() => ({}))) as unknown;
    if (!response.ok) {
      const error = payload as ErrorPayload;
      const message = error.error ?? `Error HTTP ${response.status}.`;
      const fix = error.fix ?? 'Revisa la consola de extrude serve.';
      if (error.issues) throw new PlanRejectedError(message, fix, error.issues);
      throw new ConversionError(message, fix);
    }
    return payload as T;
  }

  library(): Promise<Library> {
    return this.call<Library>('/api/library');
  }

  /** Registra un GLB base abierto desde disco: devuelve el trabajo donde materializarlo. */
  async uploadModel(bytes: ArrayBuffer): Promise<string> {
    const created = await this.call<{ job: string }>('/api/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: bytes,
    });
    return created.job;
  }

  applyPlan(job: string, plan: MaterialPlan, preview: boolean): Promise<ApplyResult> {
    return this.call<ApplyResult>(`/api/jobs/${job}/materials${preview ? '?preview=1' : ''}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(plan),
    });
  }

  /** Último plan confirmado del trabajo, o null si aún no hay ninguno. */
  async confirmedPlan(job: string): Promise<MaterialPlan | null> {
    const response = await fetch(this.url(`/api/jobs/${job}/materials.json`)).catch(() => null);
    if (!response?.ok) return null;
    return (await response.json()) as MaterialPlan;
  }
}
