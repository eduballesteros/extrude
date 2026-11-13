# Altura por capa, capturas e imágenes con IA

Funciones del visor que ajustan el modelo y producen imágenes a partir de vistas guardadas.
La generación de imágenes **no edita el DWG/DXF ni la geometría del proyecto**.

## Altura por capa (panel Capas)

```
altura escrita → pendiente (navegador) → Aplicar → YAML actualizado → reconversión
→ el resultado anterior se sustituye solo si termina bien → modelo nuevo en el visor
```

- **Altura efectiva**: la del modelo cargado (`extras.objects.height` de cada nodo de capa).
- **Guardada**: fijada en el YAML del proyecto. Vive en un bloque gestionado al principio de
  `layers:` (entre `# >>> extrude-visor` y `# <<< extrude-visor`): una regla por capa que encaja
  exactamente con su nombre y fija `height`. La regla original no se toca; quitar la altura borra
  su entrada. Si `layers:` está escrito como una lista en bloque que permite insertar esas
  marcas, el resto del archivo (comentarios, orden) se conserva (`layer_heights.py`).
- **Pendiente**: lo escrito y no aplicado; se guarda por proyecto en `localStorage`.
- La reconversión usa el plano guardado con el trabajo (`input.dwg|dxf`) y las mismas opciones
  (teselas, compresión). Se genera en `out.next/` y se releva con `out/` bajo el candado del
  proyecto; si falla, no cambia nada.
- **Se pierden** las revisiones confirmadas (materiales, ediciones con IA): parten de la
  geometría anterior. El visor lo avisa y pide un segundo clic.
- Un modelo abierto desde un GLB o un ejemplo no tiene plano: el panel lo explica.

| Método y ruta | Función |
|---|---|
| `GET /api/jobs/<id>/layers-config` | Altura por capa según el YAML, si es repetible y por qué no |
| `POST /api/jobs/<id>/reconvert` | `{heights: {capa: m \| null}}` → 202 y estado en `/status` |
| `GET /api/jobs/<id>/result` | Resultado de la última conversión (para «Reabrir el último proyecto») |

**YAML en línea y reserialización.** Si no se puede insertar el bloque gestionado (por ejemplo,
`layers: [{match: EDIF, kind: building, height: 9}]`), se reescribe el YAML completo y se añade
un comentario al principio indicándolo. Se pierden los comentarios originales y el formato.
Actualmente esta ruta tampoco conserva las marcas de los ajustes: la altura aplicada puede
no aparecer como «Guardada» y quitarla desde el panel no recupera la regla original. Antes de
aplicar ajustes, usa una lista en bloque bajo `layers:` y guarda una copia del YAML. Si ya se
reescribió, recupera la configuración anterior o elimina manualmente la regla prioritaria
añadida antes de volver a convertir.

## Selección de capa y lupa de medida

- Pulsar el nombre de una capa la resalta: copias ligeras de sus mallas (misma geometría y
  matriz) con un tinte, sin tocar materiales. Vale para mallas fusionadas, bloques instanciados,
  líneas y teselas. Con la capa oculta no se dibuja; si la capa desaparece tras reconvertir, la
  selección se quita y se avisa. Los resaltados no salen en capturas ni miniaturas.
- La lupa (`scene/loupe.ts`) copia el fotograma recién dibujado alrededor del cursor en píxeles
  reales del lienzo y marca el punto que se usará. `snapHit` ajusta a vértice (12 px), borde
  (7 px) o superficie; lo usan por igual la lupa y el clic.

## Capturas y biblioteca

- **HD** (1920 × 1080) y **4K** (3840 × 2160) son cajas: la captura es el mayor tamaño de la
  proporción de la vista que cabe en ellas. Se renderiza la vista guardada a ese tamaño real y
  el visor vuelve a su estado antes del siguiente fotograma.
- `ImageLibrary` (`viewer/src/library/images.ts`) es la única lista de imágenes; el panel de
  vistas y la biblioteca del menú principal pintan desde ella.
- Almacenamiento: IndexedDB `extrude-viewer` v2, almacén `images` (Blob + metadatos, índice por
  proyecto = nombre del modelo). Nada se sobrescribe: cada captura o generación es un registro
  nuevo. Nombres: `proyecto_vista_tipo_ANCHOxALTO_fecha.ext`, con tipo `captura`, `ia`,
  `prueba` y sufijo `-ampliada`.
- Si IndexedDB falla, la biblioteca sigue en memoria y lo avisa. Un registro sin archivo se
  muestra como «archivo no disponible».

## Generación de imágenes

La entrada son capturas de vistas (hasta 4, reducidas a 2048 px de lado) y el prompt libre del
usuario, que se envía tal cual; la frase de fidelidad es opcional y visible.

### Configuración del servidor

```bash
cd pipeline
uv sync --extra ai                 # SDK de OpenAI y Anthropic
export OPENAI_API_KEY=…            # OpenAI (GPT Image, images.edit)
export GEMINI_API_KEY=…            # Google Gemini (imagen)
export ANTHROPIC_API_KEY=…         # opcional: «Mejorar con Claude»
uv run extrude serve
uv run extrude serve --ai-simulated   # + proveedor «Simulado»: PNG de prueba, sin red ni coste
```

| Variable | Por defecto | Uso |
|---|---|---|
| `OPENAI_API_KEY` | — | Activa OpenAI |
| `EXTRUDE_OPENAI_IMAGE_MODEL` | `gpt-image-2` | Modelo propuesto primero |
| `GEMINI_API_KEY` | — | Activa Gemini |
| `EXTRUDE_GEMINI_IMAGE_MODEL` | `gemini-3.1-flash-image-preview` | Modelo propuesto primero |
| `ANTHROPIC_API_KEY` | — | Activa la sugerencia de prompt |
| `EXTRUDE_PROMPT_MODEL` | `claude-fable-5-2` | Modelo de Claude que analiza las vistas |
| `EXTRUDE_AI_SIMULATED` | — | `1` equivale a `--ai-simulated` |

Las claves solo existen en el servidor y solo viajan en la cabecera hacia su proveedor.

### Resoluciones

| Proveedor | Qué se pide | Nativo |
|---|---|---|
| OpenAI `gpt-image-2*` | `ANCHOxALTO` libre, lados múltiplos de 16, relación 1:3 a 3:1 | HD y 4K si el modelo lo admite; si no, el proveedor rechaza y se muestra |
| OpenAI `gpt-image-1`, `1.5` | 1024×1024, 1536×1024 o 1024×1536 | No llega a HD ni 4K: se indica «no nativo» |
| Gemini | `aspectRatio` más próximo + `imageSize` 1K / 2K / 4K | Las dimensiones exactas las decide el modelo |
| Simulado | Tamaño exacto pedido | Imagen de prueba, no IA |

Siempre se guarda y se muestra la **resolución real del archivo recibido** (leída de su
cabecera en el servidor y de la imagen en el navegador). Si es menor que la pedida se marca.
La ampliación a 4K es una interpolación en el navegador: se guarda aparte y se etiqueta
«Ampliada», nunca como nativa.

### API

| Método y ruta | Función |
|---|---|
| `GET /api/images/providers` | Proveedores, modelos, límites y disponibilidad (sin claves) |
| `POST /api/images/plan` | Qué se pediría para estándar, HD y 4K con esa vista; sin coste |
| `POST /api/images/suggest` | Claude propone un prompt a partir de las vistas; no genera |
| `POST /api/images/generate` | 202: una llamada al proveedor en segundo plano |
| `GET /api/images/jobs/<id>[/file]` | Estado / archivo recibido |
| `POST /api/images/jobs/<id>/cancel` | Deja de esperar y descarta el resultado |

- Una pulsación = como mucho una llamada; sin reintentos automáticos (`max_retries=0`).
  `requestId` hace idempotente el envío: un doble clic o un reenvío tras un corte de red
  devuelve el mismo trabajo. Un reintento del usuario tras un fallo es una solicitud nueva.
- Dos generaciones a la vez por servidor; en modo compartido cuentan en el límite de consultas
  de IA por usuario y hora, y cada trabajo solo lo ve quien lo pidió.
- Cancelar no detiene al proveedor: puede terminar y facturar.
- Registro en `<jobs>/_images/log.jsonl`: proveedor, modelo, estado y dimensiones; sin prompt ni
  imágenes. El servidor conserva las 30 últimas imágenes; la copia duradera es la del navegador.

## Limitaciones

- **Fidelidad geométrica:** ningún proveedor garantiza conservar volumetría, huecos ni encuadre.
  El visor lo advierte y ofrece la comparación con la captura original.
- **Proveedores reales no probados:** OpenAI, Gemini y Claude se han probado contra servidores
  locales que imitan sus API (forma de la petición, errores, una sola llamada), no con llamadas
  reales. Los límites de tamaño de `gpt-image-2` no están en su referencia pública.
- **Persistencia:** vistas e imágenes viven en el navegador (IndexedDB), no en el servidor ni
  en la cuenta del usuario. «Reabrir el último proyecto» recuerda solo el último.
- **Altura por capa:** fija una altura única para toda la capa y descarta las revisiones
  confirmadas del proyecto.
