/**
 * Borrador del plan de materiales en el visor (materials.json, ver
 * pipeline/src/extrude/materiality.py). Reglas de docs/REQUISITOS_MATERIALIDAD_IA.md §5.3:
 *
 * - Una asignación nueva prevalece sobre las anteriores para los mismos objeto + superficie:
 *   se retiran de las anteriores (que desaparecen si se quedan vacías). Así el borrador nunca
 *   tiene conflictos que el servidor tuviera que resolver por orden.
 * - Deshacer y rehacer cubren al menos las últimas 50 operaciones (RF-14 pide 20).
 */
import type { SurfaceKey } from './catalog';

export interface Parameters {
  tileSizeM?: [number, number];
  rotationDeg?: number;
  roughness?: number;
  color?: string;
}

export interface Assignment {
  objectIds: string[];
  surface: SurfaceKey;
  materialId: string;
  materialVersion: string;
  parameters: Parameters;
  origin: 'manual' | 'ai_reviewed';
  reason?: string;
}

/** Trazabilidad opcional de una propuesta de IA revisada (sin credenciales ni contexto). */
export interface AiTrace {
  provider: string;
  model: string;
  simulated?: boolean;
  promptVersion: string;
  summary: string;
  explicitRequirements?: string[];
  assumptions?: string[];
  createdAt?: string;
}

export interface Omission {
  text: string;
  reason?: string;
}

export interface MaterialPlan {
  schemaVersion: 1;
  projectId: string;
  geometryRevision: string;
  materialRevision: string;
  catalogVersion: string;
  assignments: Assignment[];
  omissions: Omission[];
  /** Descripción original (opcional: se puede borrar conservando las asignaciones, RS-10). */
  description?: string;
  ai?: AiTrace;
}

export const MAX_HISTORY = 50;

function clone(list: Assignment[]): Assignment[] {
  return list.map((a) => ({
    ...a,
    objectIds: [...a.objectIds],
    parameters: {
      ...a.parameters,
      ...(a.parameters.tileSizeM ? { tileSizeM: [...a.parameters.tileSizeM] } : {}),
    },
  }));
}

export class PlanEditor {
  private list: Assignment[] = [];
  private undoStack: Assignment[][] = [];
  private redoStack: Assignment[][] = [];
  private confirmed = '[]';
  private readonly listeners = new Set<() => void>();

  get assignments(): readonly Assignment[] {
    return this.list;
  }

  get canUndo(): boolean {
    return this.undoStack.length > 0;
  }

  get canRedo(): boolean {
    return this.redoStack.length > 0;
  }

  /** Asignaciones del último estado confirmado (copia). */
  get confirmedAssignments(): Assignment[] {
    return JSON.parse(this.confirmed) as Assignment[];
  }

  /** Hay cambios respecto al último estado confirmado. */
  get dirty(): boolean {
    return JSON.stringify(this.list) !== this.confirmed;
  }

  onChange(listener: () => void): void {
    this.listeners.add(listener);
  }

  private commit(next: Assignment[]): void {
    this.undoStack.push(clone(this.list));
    if (this.undoStack.length > MAX_HISTORY) this.undoStack.shift();
    this.redoStack = [];
    this.list = next;
    this.notify();
  }

  private notify(): void {
    this.listeners.forEach((l) => l());
  }

  /** Añade una asignación; los mismos objeto + superficie salen de las anteriores. */
  add(assignment: Assignment): void {
    if (!assignment.objectIds.length) return;
    const ids = new Set(assignment.objectIds);
    const next = clone(this.list)
      .map((a) =>
        a.surface === assignment.surface
          ? { ...a, objectIds: a.objectIds.filter((id) => !ids.has(id)) }
          : a,
      )
      .filter((a) => a.objectIds.length > 0);
    next.push(clone([assignment])[0]!);
    this.commit(next);
  }

  remove(index: number): void {
    if (index < 0 || index >= this.list.length) return;
    this.commit(clone(this.list).filter((_, i) => i !== index));
  }

  /** Sustituye todo el borrador (importar un materials.json); se puede deshacer. */
  replace(assignments: Assignment[]): void {
    this.commit(clone(assignments));
  }

  undo(): void {
    const previous = this.undoStack.pop();
    if (!previous) return;
    this.redoStack.push(clone(this.list));
    this.list = previous;
    this.notify();
  }

  redo(): void {
    const next = this.redoStack.pop();
    if (!next) return;
    this.undoStack.push(clone(this.list));
    this.list = next;
    this.notify();
  }

  /** Fija el estado confirmado (tras confirmar o al abrir un plan guardado). */
  setConfirmed(assignments: Assignment[], resetHistory = false): void {
    this.confirmed = JSON.stringify(assignments);
    if (resetHistory) {
      this.list = clone(assignments);
      this.undoStack = [];
      this.redoStack = [];
    }
    this.notify();
  }

  /** Vuelve al último estado confirmado (descartar la previsualización); se puede deshacer. */
  revert(): void {
    this.commit(JSON.parse(this.confirmed) as Assignment[]);
  }

  toPlan(
    meta: Omit<MaterialPlan, 'schemaVersion' | 'assignments' | 'omissions'>,
    omissions: Omission[] = [],
  ): MaterialPlan {
    return { schemaVersion: 1, ...meta, assignments: clone(this.list), omissions: [...omissions] };
  }
}
