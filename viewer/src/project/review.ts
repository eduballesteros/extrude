/**
 * Revisión de un proyecto convertido: apoyo de cada capa sobre el terreno, capas ignoradas
 * pendientes de clasificar, alturas provisionales y zócalos altos; y los cambios que el visor
 * puede pedir para resolverlo (los mismos ajustes por capa que la altura).
 *
 * Nada de esto edita el plano: el servidor actualiza el YAML del proyecto, vuelve a convertir
 * y solo sustituye el resultado si termina bien. El resultado anterior queda guardado.
 */

export type LayerKind =
  'building' | 'road' | 'water' | 'vegetation' | 'terrain' | 'line' | 'ignore';

export const KIND_OPTIONS: { value: LayerKind; label: string }[] = [
  { value: 'building', label: 'Edificios' },
  { value: 'road', label: 'Viales' },
  { value: 'water', label: 'Agua' },
  { value: 'vegetation', label: 'Vegetación' },
  { value: 'terrain', label: 'Terreno (curvas o puntos)' },
  { value: 'line', label: 'Líneas 3D' },
  { value: 'ignore', label: 'No convertir' },
];

/** Ajustes de una capa; `null` quita ese ajuste. */
export interface LayerAdjust {
  height?: number | null;
  support?: 'auto' | null;
  kind?: LayerKind | null;
}

export interface ProjectChanges {
  heights?: Record<string, number | null>;
  adjust?: Record<string, LayerAdjust>;
}

export interface SupportLayer {
  layer: string;
  kind: string;
  /** auto | terrain | elevation | fixed */
  baseMode: string;
  objects: number;
  checked: number;
  floating: number;
  buried: number;
  partialGap: number;
  intersecting: number;
  partiallyBuried: number;
  extrapolated: number;
  foundations: number;
  highFoundations: number;
  maxFoundationM: number;
  provisionalHeights: number;
  /** Se puede pasar al apoyo automático. */
  canRepair: boolean;
  /** Extrude lo propone: la base no la eligió el usuario y hay objetos mal apoyados. */
  recommended: boolean;
  note: string;
}

export interface PendingLayer {
  name: string;
  summary: string;
  suggested_kind: LayerKind | null;
  evidence: { closed_outlines: number; hatches: number; meshes: number };
}

export interface TerrainInfo {
  /** valid | invalid | absent | unknown */
  status: string;
  reason: string | null;
  layers?: string[];
  z_min_m?: number;
  z_max_m?: number;
  conflicting_duplicates?: number;
}

export interface QualityTotals {
  converted_layers: number;
  converted_objects: number;
  dropped_objects: number;
  ignored_layers: number;
  pending_layers: number;
  pending_elements: number;
  verified_heights: number;
  inferred_heights: number;
  provisional_heights: number;
  foundations: number;
  high_foundations: number;
  max_foundation_height_m: number;
  ground: Record<string, number>;
}

/** Qué código ejecuta el servidor (ver build_info.py). */
export interface BuildStatus {
  id: string;
  source: string;
  /** El proceso arrancó antes de que cambiara el código del disco. */
  stale: boolean;
  started: string;
  /** Solo en servidores locales: un servidor compartido no lo publica. */
  pid?: number;
}

export interface ProjectReview {
  job: string;
  revision: string | null;
  repeatable: boolean;
  terrain: TerrainInfo;
  summary: QualityTotals | null;
  support: SupportLayer[];
  /** null: informe de una versión anterior, que no analizaba las capas ignoradas. */
  pending: PendingLayer[] | null;
  outdated: boolean;
  adjustments: Record<string, LayerAdjust>;
  canRestore: boolean;
  configGenerated: boolean;
  build: BuildStatus;
  busy?: boolean;
}

export interface ServerProject {
  job: string;
  name: string;
  modified: string;
  revision: string | null;
  floating: number;
  buried: number;
  outdated: boolean;
}

/**
 * La conversión terminó en el servidor pero el visor no pudo cargar su resultado: el proyecto
 * está actualizado, la escena no. Se puede reintentar la carga sin volver a convertir.
 */
export class ProjectLoadError extends Error {
  readonly generated = true;
}

/** Texto del problema si el modelo cargado no es de la revisión esperada; null si coincide. */
export function revisionMismatch(
  expected: string | null | undefined,
  loaded: string | null | undefined,
): string | null {
  if (!expected || !loaded || expected === loaded) return null;
  const short = (revision: string): string => revision.replace(/^sha256:/, '').slice(0, 10);
  return `El modelo recibido es de la revisión ${short(loaded)} y el informe de la ${short(expected)}: no corresponden al mismo resultado.`;
}

/** Aviso sobre el servidor: código desfasado o anterior a este visor; null si está al día. */
export function serverNotice(build: BuildStatus | undefined | null): string | null {
  if (!build) {
    return 'El servidor de Extrude en marcha es anterior a este visor (no informa de su versión): reinícialo para que las importaciones y las reparaciones usen el código actual.';
  }
  if (build.stale) {
    return `El servidor de Extrude arrancó el ${build.started.replace('T', ' a las ')} y su código ha cambiado después: sigue ejecutando la versión anterior. Reinícialo para aplicar las correcciones.`;
  }
  return null;
}

export interface ReviewIssue {
  kind: 'support' | 'pending' | 'provisional' | 'inferred' | 'foundation' | 'terrain' | 'outdated';
  text: string;
}

/** Lo que queda por revisar en el proyecto, en frases para el usuario. */
export function reviewIssues(review: ProjectReview): ReviewIssue[] {
  const issues: ReviewIssue[] = [];
  const count = (key: 'floating' | 'buried' | 'partialGap'): number =>
    review.support.reduce((sum, layer) => sum + layer[key], 0);
  const floating = count('floating');
  const buried = count('buried');
  const gaps = count('partialGap');
  if (floating || buried || gaps) {
    const parts = [
      floating ? `${floating} flotan` : '',
      gaps ? `${gaps} con contacto parcial` : '',
      buried ? `${buried} enterrados` : '',
    ].filter(Boolean);
    issues.push({ kind: 'support', text: `Apoyo en el terreno: ${parts.join(', ')}.` });
  }
  if (review.terrain.status === 'invalid') {
    issues.push({
      kind: 'terrain',
      text: `El terreno no sirve para apoyar: ${review.terrain.reason ?? 'no se puede triangular'}.`,
    });
  }
  if (review.pending?.length) {
    const elements = review.pending.reduce(
      (sum, p) => sum + p.evidence.closed_outlines + p.evidence.hatches + p.evidence.meshes,
      0,
    );
    issues.push({
      kind: 'pending',
      text: `${review.pending.length} ${review.pending.length === 1 ? 'capa no convertida tiene' : 'capas no convertidas tienen'} geometría (${elements} elementos): decide qué son.`,
    });
  }
  const provisional = review.support.reduce((sum, layer) => sum + layer.provisionalHeights, 0);
  if (provisional) {
    issues.push({
      kind: 'provisional',
      text: `${provisional} edificios tienen una altura provisional (el plano no la indica).`,
    });
  }
  const inferred = review.summary?.inferred_heights ?? 0;
  if (inferred) {
    issues.push({
      kind: 'inferred',
      text: `${inferred} edificios toman su altura de la diferencia entre la cota Z de su huella y su base: es una inferencia, compruébala.`,
    });
  }
  const high = review.support.reduce((sum, layer) => sum + layer.highFoundations, 0);
  if (high) {
    issues.push({
      kind: 'foundation',
      text: `${high} edificios necesitan un zócalo de más de 3 m: revisa su implantación.`,
    });
  }
  if (review.outdated) {
    issues.push({
      kind: 'outdated',
      text: 'Este proyecto se convirtió con una versión anterior: al reconvertirlo se analizarán también las capas ignoradas.',
    });
  }
  return issues;
}
