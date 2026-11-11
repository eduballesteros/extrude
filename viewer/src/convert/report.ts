/** Campos de report.json que consume el visor; opcionales para servidores anteriores. */
export interface LayerReport {
  heights?: Record<string, number>;
  ambiguous_heights?: number;
  nearby_text_heights?: number;
  dropped_without_height?: number;
  terrain_foundations?: number;
  max_foundation_height_m?: number;
  terrain_coverage?: {
    checked: number;
    covered: number;
    extrapolated: number;
    unknown: number;
  } | null;
  ground_check?: {
    checked: number;
    floating: number;
    buried: number;
    intersecting?: number;
    partially_buried?: number;
  } | null;
}

export interface ConversionReport {
  warnings: string[];
  output: { triangles: number; total_size_bytes: number };
  timings_s: Record<string, number>;
  layers?: Record<string, LayerReport>;
}

export function summarizeQuality(report: ConversionReport) {
  const layers = Object.values(report.layers ?? {});
  const total = (value: number | undefined): number =>
    typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : 0;
  return {
    available: report.layers !== undefined,
    defaultHeights: layers.reduce((n, l) => n + total(l.heights?.['default_height']), 0),
    ambiguous: layers.reduce((n, l) => n + total(l.ambiguous_heights), 0),
    dropped: layers.reduce((n, l) => n + total(l.dropped_without_height), 0),
    nearby: layers.reduce((n, l) => n + total(l.nearby_text_heights), 0),
    groundAvailable: layers.some((l) => l.ground_check != null),
    checked: layers.reduce((n, l) => n + total(l.ground_check?.checked), 0),
    floating: layers.reduce((n, l) => n + total(l.ground_check?.floating), 0),
    buried: layers.reduce((n, l) => n + total(l.ground_check?.buried), 0),
    intersecting: layers.reduce((n, l) => n + total(l.ground_check?.intersecting), 0),
    partiallyBuried: layers.reduce((n, l) => n + total(l.ground_check?.partially_buried), 0),
    foundations: layers.reduce((n, l) => n + total(l.terrain_foundations), 0),
    extrapolated: layers.reduce((n, l) => n + total(l.terrain_coverage?.extrapolated), 0),
    coverageAvailable: layers.some((l) => l.terrain_coverage != null),
    maxFoundationHeight: Math.max(0, ...layers.map((l) => total(l.max_foundation_height_m))),
  };
}
