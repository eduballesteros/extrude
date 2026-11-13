/**
 * Editar el proyecto con IA (Fase 3) desde el visor, a través de `extrude serve`.
 *
 * - El visor nunca ve claves ni llama al proveedor: todo pasa por el servidor.
 * - Una llamada por pulsación explícita; escribir, seleccionar o mover la cámara no consultan.
 * - Todas las peticiones HTTP tienen tiempo límite y se pueden cancelar; las respuestas tardías
 *   (tras cancelar o cambiar de modelo) se descartan con `RequestGate`.
 * - La autorización se vincula al proveedor y al contenido revisado (`contextHash`): se envía
 *   exactamente la solicitud que el usuario vio.
 * - Un plan es un borrador: aplicar usa el plan guardado en el servidor y solo las operaciones
 *   marcadas; resolver dudas o dar datos (altura de planta) no vuelve a consultar.
 */
import { ConversionError } from '../convert/client';
import type { MaterialPlan } from './plan';
import type { ViewerEffects } from './viewerOps';

export interface AiProviderInfo {
  id: string;
  name: string;
  model: string | null;
  simulated: boolean;
  available: boolean;
  reason: string | null;
  limitsCheck: 'ok' | 'unknown' | 'exceeds';
  limitsNote: string | null;
  pricing: { inputPerMTok: number; outputPerMTok: number; currency: string } | null;
}

export interface AiLimits {
  maxDescriptionChars: number;
  maxInputTokens: number;
  maxOutputTokens: number;
  maxObjects: number;
  sessionRequests: number;
  timeoutS: number;
}

export interface AiProviders {
  providers: AiProviderInfo[];
  limits: AiLimits;
  promptVersion: string;
  schemaVersion: number;
}

export type AiScope = 'all' | 'selection' | 'layers';

export interface ProposalRequest {
  provider: string;
  description: string;
  scope: AiScope;
  selection: string[];
  layers: string[];
  baseRevision: string | null;
  consent: boolean;
  consentHash?: string;
  continueAfterLimit: boolean;
}

export type OperationStatus =
  'ready' | 'needs_resolution' | 'needs_input' | 'blocked' | 'incompatible';

export interface PlanOperation {
  id: string;
  tool: string;
  toolVersion: string;
  title: string;
  executor: 'server' | 'viewer';
  args: Record<string, unknown>;
  objectIds: string[];
  pending: string | null;
  dependsOn: string[];
  basis: 'explicit' | 'assumption';
  reason: string;
  status: OperationStatus;
  messages: string[];
  incompatible: { id: string; label: string; reason: string }[];
  keptManual: string[];
  /** Datos que el usuario debe dar (altura de planta, cota de apoyo…): nunca se inventan. */
  inputsNeeded?: InputNeeded[];
  impact: {
    objects: number;
    labels: string[];
    heights?: { label: string; from: number | null; to: number | null }[];
    before?: string[];
  };
}

export interface InputNeeded {
  key: string;
  label: string;
  unit: string;
  min: number;
  max: number;
}

/** Datos dados en el visor por operación: { op1: { baseZM: 0 } }. */
export type OperationInputs = Record<string, Record<string, number>>;

/** Lo que pide una operación «needs_input» (las antiguas solo pedían la altura de planta). */
export function neededInputs(op: PlanOperation): InputNeeded[] {
  if (op.inputsNeeded?.length) return op.inputsNeeded;
  return [{ key: 'floorHeightM', label: 'Altura de planta', unit: 'm', min: 2, max: 10 }];
}

export interface PendingItem {
  id: string;
  reference: string;
  question: string;
  candidateObjectIds: string[];
  rule: Record<string, unknown> | null;
  operations: string[];
}

export interface EditPlan {
  schemaVersion: number;
  planId: string;
  baseRevision: string;
  geometryRevision: string;
  materialRevision: string | null;
  catalogVersion: string;
  provider: string;
  model: string;
  simulated: boolean;
  promptVersion: string;
  createdAt: string | null;
  scope: { kind: AiScope; objectsSent: number; objectsInModel: number };
  summary: string;
  explicitRequirements: string[];
  assumptions: string[];
  operations: PlanOperation[];
  pending: PendingItem[];
  conflicts: {
    source: 'ai' | 'proposal' | 'manual';
    message: string;
    objectIds: string[];
    operations: string[];
  }[];
  unsupported: { request: string; reason: string; capability: string; capabilityTitle: string }[];
  validation: Record<string, number | boolean>;
  cached: boolean;
  usage: { inputTokens: number | null; outputTokens: number | null } | null;
  durationMs: number | null;
  costEstimate: { amount: number; currency: string } | null;
  session: { requests: number; limit: number };
}

export interface ContextPreview {
  provider: string;
  model: string;
  system: string;
  user: unknown;
  contextHash: string;
  baseRevision: string;
  estimatedInputTokens: number;
  maxOutputTokens: number;
  objectsSent: number;
  objectsInModel: number;
  cached: boolean;
  notSent: string[];
}

export interface ViewerOp {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  objectIds: string[];
}

export interface OperationResult {
  id: string;
  tool: string;
  status: 'done' | 'viewer' | 'failed' | 'not_applied' | 'pending';
  message: string;
  objectIds: string[];
}

export interface ProjectStatus {
  revision: string;
  geometryRevision: string;
  materialRevision: string | null;
  canUndo: boolean;
  canRedo: boolean;
  modelUrl: string;
  files: { base: string; materialized?: string; plan?: string };
  history: {
    id: string;
    parent: string | null;
    kind: string;
    summary: string;
    createdAt: string;
    viewer: ViewerEffects | null;
  }[];
}

export interface ApplyBody {
  planId: string;
  baseRevision: string;
  operations: string[];
  resolutions: Record<string, string[]>;
  inputs: OperationInputs;
  description?: string;
  /** Efectos del visor preparados antes de confirmar; se guardan con la revisión. */
  viewer?: ViewerEffects;
}

export interface ApplyResult {
  preview: boolean;
  url?: string;
  revision?: string;
  results: OperationResult[];
  viewerOps: ViewerOp[];
  warnings: string[];
  geometryChanged: boolean;
  project?: ProjectStatus;
  plan?: MaterialPlan | null;
}

/** Error del servidor con un `kind` estable (consent, session_limit, stale_plan…). */
export class AiRequestError extends ConversionError {
  constructor(
    message: string,
    fix: string,
    readonly kind: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message, fix);
  }
}

/** Tiempos límite (ms): consultas cortas y la propuesta (límite del servidor + margen). */
export const TIMEOUTS = { short: 15_000, apply: 120_000, propose: 90_000 } as const;

function isProject(p: Record<string, unknown>): boolean {
  return typeof p['revision'] === 'string' && Array.isArray(p['history']);
}

/** Texto del cuerpo; se rechaza en cuanto la señal se aborta (aunque el cuerpo no termine). */
function readBody(response: Response, signal: AbortSignal): Promise<string> {
  if (signal.aborted) return Promise.reject(new DOMException('aborted', 'AbortError'));
  return new Promise<string>((resolve, reject) => {
    const abort = (): void => reject(new DOMException('aborted', 'AbortError'));
    signal.addEventListener('abort', abort, { once: true });
    response.text().then(
      (text) => {
        signal.removeEventListener('abort', abort);
        resolve(text);
      },
      (error: unknown) => {
        signal.removeEventListener('abort', abort);
        reject(error instanceof Error ? error : new Error(String(error)));
      },
    );
  });
}

/** Señal que se aborta al cancelar (signal) o al vencer el plazo; `clear` quita el temporizador. */
function withTimeout(
  signal: AbortSignal | undefined,
  ms: number,
): { signal: AbortSignal; clear: () => void } {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new DOMException('timeout', 'TimeoutError')), ms);
  const forward = (): void => controller.abort(signal?.reason);
  if (signal?.aborted) forward();
  else signal?.addEventListener('abort', forward, { once: true });
  return {
    signal: controller.signal,
    clear: () => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', forward);
    },
  };
}

export class AiClient {
  constructor(readonly server: string) {}

  private async call<T>(
    path: string,
    options: {
      body?: unknown;
      signal?: AbortSignal | undefined;
      timeoutMs?: number;
      /** Comprueba el sobre de una respuesta correcta antes de entregarlo a la interfaz. */
      expect?: (payload: Record<string, unknown>) => boolean;
    } = {},
  ): Promise<T> {
    const timed = withTimeout(options.signal, options.timeoutMs ?? TIMEOUTS.short);
    const signal = timed.signal;
    const init: RequestInit = { signal };
    if (options.body !== undefined) {
      init.method = 'POST';
      init.headers = { 'Content-Type': 'application/json' };
      init.body = JSON.stringify(options.body);
    }
    let response: Response;
    try {
      response = await fetch(new URL(path, this.server), init);
    } catch (error) {
      timed.clear();
      if (options.signal?.aborted) throw error;
      if (signal.aborted) {
        throw new AiRequestError(
          'El servidor no respondió a tiempo.',
          'Reintenta. Si era una propuesta, el proveedor puede haberla terminado (quedaría en caché).',
          'timeout',
        );
      }
      throw new ConversionError(
        `No se pudo contactar con el servidor (${this.server}).`,
        'Arráncalo con: cd pipeline && uv run extrude serve',
      );
    }
    // El plazo y la cancelación cubren también la lectura del cuerpo (puede quedarse a medias)
    let payload: Record<string, unknown>;
    try {
      const text = await readBody(response, signal);
      payload = text.trim() ? (JSON.parse(text) as Record<string, unknown>) : {};
    } catch (error) {
      if (options.signal?.aborted) throw error;
      if (signal.aborted) {
        throw new AiRequestError(
          'El servidor no terminó de responder a tiempo.',
          'Reintenta. Si era una propuesta, el proveedor puede haberla terminado (quedaría en caché).',
          'timeout',
        );
      }
      if (!response.ok) payload = {};
      else {
        throw new AiRequestError(
          'El servidor devolvió una respuesta que no es JSON válido.',
          'Revisa la consola de extrude serve.',
          'invalid_response',
        );
      }
    } finally {
      timed.clear();
    }
    if (!response.ok) {
      const { error, fix, kind, ...details } = payload;
      throw new AiRequestError(
        typeof error === 'string' ? error : `Error HTTP ${response.status}.`,
        typeof fix === 'string' ? fix : 'Revisa la consola de extrude serve.',
        typeof kind === 'string' ? kind : 'http',
        details,
      );
    }
    const valid =
      typeof payload === 'object' &&
      payload !== null &&
      !Array.isArray(payload) &&
      (options.expect?.(payload) ?? true);
    if (!valid) {
      throw new AiRequestError(
        'La respuesta del servidor no tiene la forma esperada.',
        'Comprueba que el visor y extrude serve son de la misma versión.',
        'invalid_response',
      );
    }
    return payload as T;
  }

  providers(): Promise<AiProviders> {
    return this.call<AiProviders>('/api/ai/providers', {
      expect: (p) => Array.isArray(p['providers']),
    });
  }

  /** Lo que se enviaría y su huella: no llama a la IA. */
  context(job: string, request: ProposalRequest): Promise<ContextPreview> {
    return this.call<ContextPreview>(`/api/jobs/${job}/ai/context`, { body: request });
  }

  propose(job: string, request: ProposalRequest, signal?: AbortSignal): Promise<EditPlan> {
    return this.call<EditPlan>(`/api/jobs/${job}/ai/propose`, {
      body: request,
      signal,
      timeoutMs: TIMEOUTS.propose,
      expect: (p) => typeof p['planId'] === 'string' && Array.isArray(p['operations']),
    });
  }

  apply(
    job: string,
    body: ApplyBody,
    preview: boolean,
    signal?: AbortSignal,
  ): Promise<ApplyResult> {
    return this.call<ApplyResult>(`/api/jobs/${job}/edit/apply${preview ? '?preview=1' : ''}`, {
      body,
      signal,
      timeoutMs: TIMEOUTS.apply,
      expect: (p) => Array.isArray(p['results']) && Array.isArray(p['viewerOps']),
    });
  }

  project(job: string): Promise<ProjectStatus> {
    return this.call<ProjectStatus>(`/api/jobs/${job}/project`, { expect: isProject });
  }

  /** Deshacer/rehacer; con `expect` solo actúa si esa es la revisión actual (compensar). */
  step(job: string, forward: boolean, expect?: string): Promise<ProjectStatus> {
    const query = expect ? `?expect=${encodeURIComponent(expect)}` : '';
    return this.call<ProjectStatus>(
      `/api/jobs/${job}/project/${forward ? 'redo' : 'undo'}${query}`,
      {
        body: {},
        timeoutMs: TIMEOUTS.apply,
        expect: isProject,
      },
    );
  }

  clearCache(job: string): Promise<{ removed: number }> {
    return this.call<{ removed: number }>(`/api/jobs/${job}/ai/cache/clear`, { body: {} });
  }
}

/**
 * Descarta respuestas tardías: cada solicitud recibe un turno; cancelar o cambiar de modelo
 * invalida los anteriores.
 */
export class RequestGate {
  private turn = 0;
  private controller: AbortController | null = null;

  get busy(): boolean {
    return this.controller !== null;
  }

  start(): { turn: number; signal: AbortSignal } {
    this.controller?.abort();
    this.controller = new AbortController();
    return { turn: ++this.turn, signal: this.controller.signal };
  }

  current(turn: number): boolean {
    return turn === this.turn;
  }

  finish(turn: number): void {
    if (turn === this.turn) this.controller = null;
  }

  cancel(): void {
    this.turn++;
    this.controller?.abort();
    this.controller = null;
  }
}

/** Huella de lo que el usuario revisó: si cambia algo relevante, la autorización no vale. */
export function requestKey(request: ProposalRequest): string {
  return JSON.stringify([
    request.provider,
    request.description,
    request.scope,
    [...request.selection].sort(),
    [...request.layers].sort(),
    request.baseRevision,
  ]);
}

/** El plan se preparó sobre la revisión que se está viendo. */
export function planIsCurrent(plan: EditPlan, revision: string | null): boolean {
  return revision !== null && plan.baseRevision === revision;
}

/** Una operación se puede aplicar con lo que el usuario ya ha resuelto (sin IA). */
export function applicable(
  op: PlanOperation,
  resolutions: Record<string, string[]>,
  inputs: OperationInputs,
): boolean {
  if (op.status === 'ready') return true;
  if (op.status === 'needs_resolution') {
    return op.pending !== null && (resolutions[op.pending]?.length ?? 0) > 0;
  }
  if (op.status === 'needs_input') {
    return neededInputs(op).every((i) => Number.isFinite(inputs[op.id]?.[i.key]));
  }
  return false;
}

/**
 * Marcar una operación marca también aquellas de las que depende; desmarcarla desmarca las que
 * dependen de ella. Así nunca se envía un conjunto dependiente incompleto.
 */
export function toggleOperation(
  plan: EditPlan,
  selected: ReadonlySet<string>,
  id: string,
  on: boolean,
): Set<string> {
  const next = new Set(selected);
  const byId = new Map(plan.operations.map((o) => [o.id, o]));
  if (on) {
    const visit = (opId: string): void => {
      if (next.has(opId)) return;
      next.add(opId);
      byId.get(opId)?.dependsOn.forEach(visit);
    };
    visit(id);
  } else {
    const remove = (opId: string): void => {
      if (!next.delete(opId)) return;
      plan.operations.filter((o) => o.dependsOn.includes(opId)).forEach((o) => remove(o.id));
    };
    remove(id);
  }
  return next;
}

/** Marcadas por defecto: lo explícito y listo; los supuestos los marca el usuario. */
export function defaultSelection(plan: EditPlan): Set<string> {
  return new Set(
    plan.operations.filter((o) => o.status === 'ready' && o.basis === 'explicit').map((o) => o.id),
  );
}

const CONSENT_PREFIX = 'extrude.ai.consent.';

/** Autorización de envío por proveedor (después de revisar el contenido), revocable. */
export const consentStore = {
  has(provider: string): boolean {
    try {
      return localStorage.getItem(CONSENT_PREFIX + provider) === 'granted';
    } catch {
      return false;
    }
  },
  grant(provider: string): void {
    try {
      localStorage.setItem(CONSENT_PREFIX + provider, 'granted');
    } catch {
      // Sin almacenamiento: se volverá a preguntar en la próxima sesión
    }
  },
  revoke(provider: string): void {
    try {
      localStorage.removeItem(CONSENT_PREFIX + provider);
    } catch {
      // nada que borrar
    }
  },
};
