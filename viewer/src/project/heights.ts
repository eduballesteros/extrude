/**
 * Altura por capa desde el panel de capas.
 *
 * - La altura EFECTIVA de una capa es la del modelo cargado (tabla `objects.height` que el
 *   pipeline escribe en cada nodo de capa del GLB): lo que se convirtió de verdad.
 * - La altura GUARDADA es la fijada en el YAML del proyecto (`/api/jobs/<id>/layers-config`).
 * - La altura PENDIENTE es lo escrito en el panel y aún no aplicado. Vive en `HeightDrafts`
 *   (memoria + localStorage por proyecto), no en los campos del panel: cerrar y abrir el panel
 *   o recargar la página la conserva.
 *
 * Aplicar envía las pendientes al servidor, que actualiza el YAML y reconvierte el plano. El
 * modelo del visor solo cambia cuando esa conversión termina bien.
 */
import type { Object3D } from 'three';
import type { BuildStatus, ProjectChanges, ProjectReview, ServerProject } from './review';

export const MAX_HEIGHT_M = 1000;
const STORAGE_PREFIX = 'extrude.heights.';

export interface LayerHeightInfo {
  name: string;
  kind: string | null;
  editable: boolean;
  reason: string | null;
  /** De dónde sale hoy la altura: viewer | fixed | text | attribute | floors | … */
  source: string | null;
  /** Altura fijada desde el visor y guardada en el YAML. */
  fixed: number | null;
  ruleHeight: number | null;
  /** auto | terrain | elevation | fixed */
  baseMode?: string | null;
  supportAdjusted?: boolean;
  kindAdjusted?: boolean;
}

export interface LayersConfig {
  repeatable: boolean;
  reason: string | null;
  layers: LayerHeightInfo[];
  heights: Record<string, number>;
  config: string | null;
  busy?: boolean;
}

export interface EffectiveHeight {
  min: number;
  max: number;
  count: number;
}

/** Alturas reales de los objetos de una capa en el modelo cargado; null si no tiene. */
export function effectiveHeight(nodes: readonly Object3D[]): EffectiveHeight | null {
  let min = Infinity;
  let max = -Infinity;
  let count = 0;
  for (const node of nodes) {
    node.traverse((child) => {
      const table = (child.userData as { objects?: { height?: unknown } }).objects?.height;
      if (!Array.isArray(table)) return;
      for (const value of table) {
        if (typeof value !== 'number' || !Number.isFinite(value)) continue;
        min = Math.min(min, value);
        max = Math.max(max, value);
        count += 1;
      }
    });
  }
  return count ? { min, max, count } : null;
}

function meters(value: number): string {
  return Number(value.toFixed(2)).toLocaleString('es-ES', { maximumFractionDigits: 2 });
}

/** «12 m», o «3–24 m» si los objetos de la capa tienen alturas distintas. */
export function formatEffective(height: EffectiveHeight | null): string {
  if (!height) return '—';
  return Math.abs(height.max - height.min) < 0.005
    ? `${meters(height.min)} m`
    : `${meters(height.min)}–${meters(height.max)} m`;
}

export type ParsedHeight = { value: number } | { error: string };

/** Valida lo escrito en el campo (admite coma decimal). */
export function parseHeight(text: string): ParsedHeight {
  const cleaned = text.trim().replace(/\s*m$/i, '').replace(',', '.');
  if (!cleaned) return { error: 'Escribe una altura en metros.' };
  if (!/^[+-]?(\d+\.?\d*|\.\d+)$/.test(cleaned)) return { error: 'No es un número: usa metros.' };
  const value = Number(cleaned);
  if (!(value > 0)) return { error: 'La altura debe ser mayor que 0 m.' };
  if (value > MAX_HEIGHT_M) return { error: `Como máximo ${MAX_HEIGHT_M} m.` };
  return { value: Math.round(value * 1000) / 1000 };
}

interface Storage_ {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

function defaultStorage(): Storage_ | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null;
  }
}

/**
 * Lo escrito en el panel y aún no aplicado, por capa (texto tal cual, también si no es válido,
 * para poder mostrar el error junto al campo). `null` = quitar la altura fijada.
 */
export class HeightDrafts {
  private project: string | null = null;
  private drafts = new Map<string, string | null>();

  constructor(private readonly storage: Storage_ | null = defaultStorage()) {}

  /** Cambia de proyecto y recupera lo que se dejó pendiente en él. */
  open(project: string | null): void {
    this.project = project;
    this.drafts = new Map();
    if (!project) return;
    try {
      const raw = this.storage?.getItem(STORAGE_PREFIX + project);
      const data: unknown = raw ? JSON.parse(raw) : null;
      if (data && typeof data === 'object') {
        for (const [layer, value] of Object.entries(data as Record<string, unknown>)) {
          if (typeof value === 'string' || value === null) this.drafts.set(layer, value);
        }
      }
    } catch {
      // Borrador ilegible: se ignora
    }
  }

  private persist(): void {
    if (!this.project) return;
    try {
      if (this.drafts.size === 0) this.storage?.removeItem(STORAGE_PREFIX + this.project);
      else {
        this.storage?.setItem(
          STORAGE_PREFIX + this.project,
          JSON.stringify(Object.fromEntries(this.drafts)),
        );
      }
    } catch {
      // Almacenamiento no disponible: lo pendiente dura lo que la página
    }
  }

  /** Texto pendiente; undefined si no hay nada pendiente para esa capa. */
  get(layer: string): string | null | undefined {
    return this.drafts.get(layer);
  }

  /** Anota lo escrito. Si coincide con lo guardado, deja de estar pendiente. */
  set(layer: string, text: string, saved: number | null): void {
    const parsed = parseHeight(text);
    if (text.trim() === '' || ('value' in parsed && parsed.value === saved)) {
      this.drafts.delete(layer);
    } else {
      this.drafts.set(layer, text);
    }
    this.persist();
  }

  /** Pide quitar la altura fijada (la capa vuelve a su regla original del YAML). */
  remove(layer: string, saved: number | null): void {
    if (saved === null) this.drafts.delete(layer);
    else this.drafts.set(layer, null);
    this.persist();
  }

  /** Recupera el valor anterior (el guardado): descarta lo pendiente de esa capa. */
  revert(layer: string): void {
    this.drafts.delete(layer);
    this.persist();
  }

  clear(): void {
    this.drafts.clear();
    this.persist();
  }

  get pending(): string[] {
    return [...this.drafts.keys()];
  }

  /** Error de validación de lo pendiente en esa capa, o null. */
  error(layer: string): string | null {
    const text = this.drafts.get(layer);
    if (text === undefined || text === null) return null;
    const parsed = parseHeight(text);
    return 'error' in parsed ? parsed.error : null;
  }

  /**
   * Cambios listos para enviar: {capa: metros | null}. Lanza si alguno no es válido o si la
   * capa ya no admite altura.
   */
  changes(editable: ReadonlySet<string>): Record<string, number | null> {
    const out: Record<string, number | null> = {};
    for (const [layer, text] of this.drafts) {
      if (!editable.has(layer)) continue; // capa que ya no existe: no se envía
      if (text === null) {
        out[layer] = null;
        continue;
      }
      const parsed = parseHeight(text);
      if ('error' in parsed) throw new Error(`${layer}: ${parsed.error}`);
      out[layer] = parsed.value;
    }
    return out;
  }
}

export class ProjectError extends Error {
  constructor(
    message: string,
    readonly fix: string,
    readonly kind?: string,
  ) {
    super(message);
  }
}

interface ServerError {
  error?: string;
  fix?: string;
  kind?: string;
}

/** Peticiones del proyecto al servidor: configuración por capa y reconversión. */
export class ProjectClient {
  constructor(private readonly server: string) {}

  url(path: string): string {
    return new URL(path, this.server).toString();
  }

  private async call<T>(path: string, init?: RequestInit): Promise<T> {
    let response: Response;
    try {
      response = await fetch(this.url(path), init);
    } catch {
      throw new ProjectError('No se pudo contactar con el servidor de Extrude.', 'Reintenta.');
    }
    const payload = (await response.json().catch(() => ({}))) as T & ServerError;
    if (!response.ok) {
      throw new ProjectError(
        payload.error ?? `Error ${response.status}`,
        payload.fix ?? '',
        payload.kind,
      );
    }
    return payload;
  }

  layersConfig(job: string): Promise<LayersConfig> {
    return this.call<LayersConfig>(`/api/jobs/${job}/layers-config`);
  }

  /** Envía los cambios; devuelve la ruta de estado de la reconversión aceptada. */
  async reconvert(job: string, changes: ProjectChanges): Promise<string> {
    const accepted = await this.call<{ status: string }>(`/api/jobs/${job}/reconvert`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(changes),
    });
    return accepted.status;
  }

  /** Qué hay que revisar en el proyecto: apoyo, capas pendientes, alturas provisionales. */
  review(job: string): Promise<ProjectReview> {
    return this.call<ProjectReview>(`/api/jobs/${job}/review`);
  }

  /** Vuelve al resultado anterior a la última reconversión; devuelve ese resultado. */
  restore<T>(job: string): Promise<T> {
    return this.call<T>(`/api/jobs/${job}/restore`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
  }

  /** Proyectos convertidos que conserva el servidor (los más recientes primero). */
  async projects(): Promise<ServerProject[]> {
    const data = await this.call<{ projects?: ServerProject[] }>('/api/projects');
    return data.projects ?? [];
  }

  /** Identidad del código que ejecuta el servidor; undefined en servidores anteriores. */
  async build(): Promise<BuildStatus | undefined> {
    const data = await this.call<{ build?: BuildStatus }>('/api/health');
    return data.build;
  }

  async configText(job: string): Promise<string> {
    const response = await fetch(this.url(`/api/jobs/${job}/config.yaml`));
    if (!response.ok) throw new ProjectError('No se pudo descargar la configuración.', '');
    return response.text();
  }
}
