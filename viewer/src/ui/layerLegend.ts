/**
 * Leyenda de capas para el prompt de «Generar con IA»: explica al generador qué es cada cosa de
 * la captura según el TIPO de cada capa (edificio, vial, agua, vegetación, terreno), que el
 * pipeline guarda en el GLB. Vale para cualquier proyecto: no depende de nombres concretos.
 * Los nombres de capa solo se citan como pista, nunca para decidir el tipo.
 */
export interface LegendLayer {
  name: string;
  kind: string;
  visible: boolean;
}

/** Cómo describir cada tipo en la imagen de referencia (orden de la frase). */
const KIND_TEXT: readonly [kind: string, text: string][] = [
  ['building', 'los volúmenes son edificios'],
  [
    'road',
    'las superficies planas a ras de suelo son viales (calles, aceras, carriles o caminos pavimentados), no muros ni edificios',
  ],
  ['water', 'las superficies de agua son láminas de agua, como un lago, un río o un estanque'],
  [
    'vegetation',
    'las zonas de vegetación son parques y zonas verdes con césped, arbustos y árboles',
  ],
  ['terrain', 'la superficie ondulada es el terreno natural del solar'],
];

const MAX_NAMES = 4;

/** Nombre de capa legible: sin prefijos de guion bajo ni separadores repetidos. */
function cleanName(name: string): string {
  return name
    .replace(/^[_\-\s.]+/, '')
    .replace(/[_]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/** La frase de leyenda, o null si no hay capas visibles de un tipo conocido. */
export function layerLegend(layers: readonly LegendLayer[]): string | null {
  const parts: string[] = [];
  for (const [kind, text] of KIND_TEXT) {
    const names = layers
      .filter((layer) => layer.visible && layer.kind === kind)
      .map((layer) => cleanName(layer.name))
      .filter((name) => name.length > 0);
    if (names.length === 0) continue;
    const shown = names.slice(0, MAX_NAMES).join(', ');
    const more = names.length > MAX_NAMES ? ` y ${names.length - MAX_NAMES} más` : '';
    parts.push(`${text} (capas ${shown}${more})`);
  }
  if (parts.length === 0) return null;
  return `Leyenda de la imagen de referencia: ${parts.join('; ')}.`;
}
