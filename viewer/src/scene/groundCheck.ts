import { Box3, type Object3D } from 'three';

/** Capas extruidas que deberían apoyarse en el terreno. */
const EXTRUDED_KINDS = new Set(['building', 'road', 'water', 'vegetation']);
/** Una base "en cota 0" (GLB antiguos sin ground_check). */
const ZERO_EPS_M = 0.01;
const TERRAIN_MARGIN_M = 1;

/** `extras.ground_check` de cada capa (pipeline ≥ esta versión): sólidos frente al TIN. */
export interface GroundCheck {
  checked: number;
  floating: number;
  buried: number;
  /** Intersecciones con ladera: informativas, no disparan consejo de reasentamiento. */
  intersecting?: number;
  partially_buried?: number;
}

export interface MisplacedLayer {
  layer: string;
  /** Recuentos exactos del pipeline, o null si se dedujo por la caja de la capa. */
  check: GroundCheck | null;
}

interface LayerNodeExtras {
  layer?: string;
  kind?: string;
  ground_check?: GroundCheck;
}

function layerNodes(root: Object3D): Map<string, { kind: string; nodes: Object3D[] }> {
  const layers = new Map<string, { kind: string; nodes: Object3D[] }>();
  root.traverse((node) => {
    const extras = node.userData as LayerNodeExtras;
    if (typeof extras.layer !== 'string') return;
    const entry = layers.get(extras.layer) ?? { kind: extras.kind ?? 'unknown', nodes: [] };
    entry.nodes.push(node);
    layers.set(extras.layer, entry);
  });
  return layers;
}

function boundsY(nodes: Object3D[]): [number, number] {
  const box = new Box3();
  for (const node of nodes) box.expandByObject(node);
  return [box.min.y, box.max.y];
}

/**
 * Capas con sólidos que flotan sobre el terreno o quedan enterrados. Solo si el modelo tiene una
 * capa `terrain`. Usa `ground_check` del pipeline; en GLB antiguos, una heurística: capa extruida
 * con base en cota 0 exacta mientras el terreno no pasa por 0 (indicio de extrusión desde 0).
 */
export function misplacedLayers(root: Object3D): MisplacedLayer[] {
  root.updateMatrixWorld(true);
  const layers = layerNodes(root);
  const terrain = [...layers.values()].filter((l) => l.kind === 'terrain');
  if (!terrain.length) return [];
  const extruded = [...layers.entries()].filter(([, l]) => EXTRUDED_KINDS.has(l.kind));
  const checks = extruded.map(([name, l]) => ({
    name,
    nodes: l.nodes,
    check: (l.nodes[0]!.userData as LayerNodeExtras).ground_check ?? null,
  }));

  if (checks.some((c) => c.check)) {
    return checks
      .filter(({ check }) => {
        if (!check) return false;
        // Mismo criterio que el informe del servidor: basta un sólido mal apoyado
        return check.floating + check.buried > 0;
      })
      .map(({ name, check }) => ({ layer: name, check }));
  }

  const [terrainLow] = boundsY(terrain.flatMap((l) => l.nodes));
  if (Math.abs(terrainLow) <= TERRAIN_MARGIN_M) return [];
  return checks
    .filter(({ nodes }) => Math.abs(boundsY(nodes)[0]) < ZERO_EPS_M)
    .map(({ name }) => ({ layer: name, check: null }));
}

/** Texto del aviso para el usuario. */
export function misplacedMessage(layers: MisplacedLayer[]): string {
  const detail = layers
    .map(({ layer, check }) =>
      check
        ? `${layer} (${check.floating} flotan, ${check.buried} enterrados de ${check.checked})`
        : layer,
    )
    .join(', ');
  return (
    `Algunos sólidos pueden estar flotando sobre el terreno o enterrados: ${detail}. ` +
    'Abre la pestaña Capas → «Revisión del proyecto» para apoyarlos en el terreno.'
  );
}
