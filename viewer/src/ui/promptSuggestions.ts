/**
 * Frases opcionales para el prompt de «Generar con IA», agrupadas por categorías. Se añaden al
 * prompt al pulsarlas (nunca se envían solas). Pensadas para renders de arquitectura realistas:
 * edificios completos y creíbles, escala humana y vida urbana, luz y clima concretos.
 */
export interface SuggestionCategory {
  id: string;
  label: string;
  phrases: readonly string[];
  /** 'start': la frase abre el prompt y sustituye a otra de la misma categoría (el rol). */
  position?: 'start';
}

export const SUGGESTION_CATEGORIES: readonly SuggestionCategory[] = [
  {
    id: 'rol',
    label: 'Rol',
    position: 'start',
    phrases: [
      'Actúa como visualizador arquitectónico experto en renders fotorrealistas',
      'Actúa como fotógrafo de arquitectura profesional que retrata obra recién terminada',
      'Actúa como estudio de visualización 3D que prepara la imagen principal de un concurso',
      'Actúa como urbanista que presenta un barrio vivo y habitado a los vecinos',
      'Actúa como paisajista que muestra el proyecto integrado en su entorno natural',
      'Actúa como ilustrador de arquitectura con un acabado artístico pero preciso',
    ],
  },
  {
    id: 'estilo',
    label: 'Estilo',
    phrases: [
      'visualización arquitectónica fotorrealista',
      'render de concurso de arquitectura, alta definición',
      'fotografía de arquitectura profesional con objetivo descentrable, verticales rectas',
      'iluminación global realista y sombras suaves de contacto',
      'colores naturales, sin saturar, balance de blancos neutro',
      'nitidez alta, sin ruido ni artefactos',
      'profundidad de campo ligera y realista',
      'estilo maqueta blanca con sombras',
      'acuarela arquitectónica de trazo suave',
      'sin texto, marcas de agua ni logotipos',
    ],
  },
  {
    id: 'edificios',
    label: 'Edificios completos',
    phrases: [
      'edificios completos y terminados, con todas sus plantas',
      'ventanas en todas las fachadas y plantas, con carpinterías y vidrios realistas',
      'reflejos realistas del cielo en los vidrios',
      'balcones y terrazas con barandillas de vidrio o metal',
      'persianas, toldos y cortinas variados en las ventanas',
      'planta baja comercial con escaparates y portales iluminados',
      'cubiertas planas con petos, lucernarios y maquinaria discreta',
      'cubiertas inclinadas de teja cerámica',
      'cubiertas ajardinadas y placas solares',
      'zócalos, cornisas y remates bien resueltos',
      'celosías y lamas de protección solar',
      'juntas, alféizares y detalles constructivos visibles',
      'ligeras diferencias de uso: macetas, ropa tendida, luces encendidas',
    ],
  },
  {
    id: 'tipos',
    label: 'Tipos de edificio',
    phrases: [
      'bloques de vivienda colectiva de 4 a 6 plantas',
      'viviendas unifamiliares adosadas con jardín delantero',
      'chalets aislados con piscina y parcela ajardinada',
      'torres residenciales esbeltas',
      'edificio de oficinas con muro cortina de vidrio',
      'colegio con patio y pistas deportivas',
      'centro de salud de una o dos plantas',
      'biblioteca y centro cívico de diseño contemporáneo',
      'polideportivo con cubierta ligera',
      'mercado municipal y pequeño centro comercial',
      'hotel con terraza en cubierta',
      'naves industriales y logísticas ordenadas',
      'edificios históricos rehabilitados junto a obra nueva',
    ],
  },
  {
    id: 'materiales',
    label: 'Materiales',
    phrases: [
      'fachadas de revoco blanco',
      'ladrillo caravista en tonos claros',
      'hormigón visto con encofrado de tablilla',
      'piedra caliza clara',
      'revestimientos de madera tratada',
      'paneles metálicos en gris antracita',
      'cerámica vidriada y celosías cerámicas',
      'fachada vegetal y jardineras integradas',
      'carpinterías oscuras de aluminio',
      'materiales claros (piedra clara, hormigón blanco, madera)',
    ],
  },
  {
    id: 'gente',
    label: 'Gente y vida urbana',
    phrases: [
      'personas paseando por las aceras',
      'gente corriendo por el paseo',
      'ciclistas por el carril bici',
      'niños jugando en el parque infantil',
      'familias y grupos de amigos en terrazas de bares',
      'personas sentadas en bancos a la sombra',
      'gente paseando perros',
      'personas comprando en los comercios de planta baja',
      'mayores charlando en una plaza',
      'personas haciendo deporte y yoga en el césped',
      'gente con bolsas de la compra y carritos de bebé',
      'escala humana realista, figuras naturales y sin deformaciones',
      'densidad de gente moderada, sin aglomeraciones',
    ],
  },
  {
    id: 'movilidad',
    label: 'Movilidad',
    phrases: [
      'coches aparcados a lo largo de la calle',
      'tráfico ligero de coches',
      'autobús urbano en una parada con marquesina',
      'tranvía circulando por una avenida verde',
      'carril bici segregado',
      'aceras anchas y pasos de cebra',
      'motos y patinetes aparcados',
      'calles peatonales de prioridad residencial',
    ],
  },
  {
    id: 'vegetacion',
    label: 'Vegetación y paisaje',
    phrases: [
      'vegetación abundante y arbolado maduro',
      'alineaciones de árboles en las calles',
      'olivos, pinos piñoneros y encinas',
      'plataneros y jacarandas en flor',
      'césped bien cuidado y praderas de flores silvestres',
      'setos, arbustos y jardines con flores',
      'huertos urbanos',
      'láminas de agua, fuentes y estanques',
      'contexto paisajístico con colinas al fondo',
      'sierra y montañas en el horizonte',
      'paisaje mediterráneo seco con matorral',
      'frente costero con el mar al fondo',
    ],
  },
  {
    id: 'mobiliario',
    label: 'Mobiliario urbano',
    phrases: [
      'farolas y luminarias urbanas',
      'bancos, papeleras y fuentes de agua potable',
      'juegos infantiles y zonas de calistenia',
      'pérgolas y zonas de sombra',
      'pavimentos de granito y adoquín',
      'alcorques con rejilla y bolardos',
      'señalización urbana discreta',
      'quioscos y terrazas con sombrillas',
    ],
  },
  {
    id: 'luz',
    label: 'Luz y hora',
    phrases: [
      'luz natural de mediodía, sol alto y sombras cortas',
      'luz natural suave de tarde',
      'luz dorada de atardecer y sombras largas',
      'hora azul con ventanas iluminadas',
      'noche con iluminación urbana cálida',
      'mañana temprano con luz suave y fresca',
      'contraluz con brillo en los bordes de los edificios',
    ],
  },
  {
    id: 'clima',
    label: 'Clima y estación',
    phrases: [
      'cielo azul despejado',
      'cielo con nubes dispersas',
      'día nublado de luz difusa',
      'después de la lluvia, con charcos y reflejos en el pavimento',
      'niebla ligera matinal',
      'otoño con hojas ocres en los árboles',
      'primavera con árboles en flor',
      'verano luminoso y caluroso',
      'invierno con nieve ligera en las cubiertas',
    ],
  },
  {
    id: 'camara',
    label: 'Cámara y encuadre',
    phrases: [
      'vista aérea de dron a media altura',
      'vista de pájaro cenital',
      'vista a la altura del peatón',
      'gran angular sin distorsión exagerada',
      'contrapicado desde la calle',
      'plano general del conjunto',
      'encuadre cercano con detalle de fachada',
    ],
  },
  {
    id: 'ambiente',
    label: 'Ambiente',
    phrases: [
      'atmósfera tranquila de barrio residencial',
      'ambiente animado de fin de semana',
      'día de mercado al aire libre',
      'barrio recién terminado pero ya habitado',
      'sensación de lugar vivido y cuidado',
      'escena acogedora y luminosa',
    ],
  },
];

/** La frase ya está en el prompt (sin distinguir mayúsculas): no se ofrece dos veces. */
export function promptHas(prompt: string, phrase: string): boolean {
  return prompt.toLocaleLowerCase('es').includes(phrase.toLocaleLowerCase('es'));
}

function upperFirst(text: string): string {
  return text.charAt(0).toLocaleUpperCase('es') + text.slice(1);
}

/** Añade la frase al final: tras un punto, como frase nueva; si no, separada por coma. */
export function appendPhrase(prompt: string, phrase: string): string {
  const base = prompt.trim();
  if (!base) return phrase;
  if (base.endsWith('.')) return `${base} ${upperFirst(phrase)}`;
  return `${base.replace(/[,;]$/, '')}, ${phrase}`;
}

/**
 * Pone `phrase` como primera frase del prompt; si ya empieza por otra de `alternatives` (otro
 * rol), la sustituye en vez de acumular varios roles.
 */
export function prependPhrase(
  prompt: string,
  phrase: string,
  alternatives: readonly string[],
): string {
  let rest = prompt.trim();
  const lower = rest.toLocaleLowerCase('es');
  const current = alternatives.find((other) => lower.startsWith(other.toLocaleLowerCase('es')));
  if (current)
    rest = rest
      .slice(current.length)
      .replace(/^[.,;:]\s*/, '')
      .trim();
  return rest ? `${phrase}. ${upperFirst(rest)}` : `${phrase}.`;
}

/** Aplica una frase de la categoría según su posición (final o principio). */
export function applySuggestion(
  prompt: string,
  category: SuggestionCategory,
  phrase: string,
): string {
  return category.position === 'start'
    ? prependPhrase(prompt, phrase, category.phrases)
    : appendPhrase(prompt, phrase);
}
