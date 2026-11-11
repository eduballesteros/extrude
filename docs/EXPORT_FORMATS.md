# Formatos de exportación

## GLB del pipeline (`out/model.glb`)

glTF 2.0 binario, generado sin librerías externas (`pipeline/src/extrude/gltf_export.py`).

### Coordenadas

- **Unidades:** metros, siempre (se convierte desde `$INSUNITS` o `units` del YAML).
- **Ejes:** glTF es Y-up. Transformación aplicada: `(x, y, z)_dwg → (x, z, -y)_gltf`.
  Es una rotación de −90° alrededor de X (determinante +1, sin espejo): el norte del dibujo
  (+Y DWG) queda hacia −Z en glTF.
- **Recentrado:** las coordenadas se calculan en float64, se les resta `origin_offset` y luego
  se guardan en float32. El origen es el centro XY del bbox exportado con Z = 0, para que las
  cotas sigan siendo absolutas.
- **Volver a coordenadas reales** (p. ej. UTM):
  ```
  x_dwg = x_gltf + origin_offset[0]
  y_dwg = -z_gltf + origin_offset[1]
  z_dwg = y_gltf + origin_offset[2]
  ```
  Verificado en tests con la muestra UTM: el error de ida y vuelta es menor de 0,1 mm. Sin
  recentrar, la resolución de float32 a Y ≈ 4 140 000 m es de 0,25–0,5 m.

### `scene.extras`

| Clave | Contenido |
|---|---|
| `origin_offset` | `[x, y, z]` en metros y **ejes DWG** (Z-up) |
| `units` | `"m"` |
| `source_units` | unidades del dibujo de origen (`mm`, `m`…) |
| `crs` | lo indicado en `crs` del YAML (p. ej. `"EPSG:25830"`) o `null`. La geometría no se reproyecta. |
| `center_lat`, `center_lon` | centro del bbox de la escena en WGS84 (grados) o `null`. Se calcula si `crs` es un EPSG UTM (WGS 84 326xx/327xx, ETRS89 258xx, NAD83 269xx) o, con `pyproj` instalado, cualquier CRS proyectado. El visor sitúa ahí el sol al cargar el modelo. |
| `axis_transform` | `"(x, y, z)_dwg -> (x, z, -y)_gltf"` |
| `bbox` | `{min, max}` en coordenadas glTF (ya recentradas) |
| `generator` | versión de Extrude |
| `geometry_revision` | `sha256:…` de la geometría construida; la misma que en `objects.json` (ver abajo) |

### Nodos

- **Un nodo raíz por capa** (`name` = nombre de la capa) con una malla fusionada: una primitiva
  de triángulos y, si la capa es `kind: line`, otra de líneas (`mode: LINES`). Un material
  por capa, con el nombre de la capa.
- `node.extras`:

  | Clave | Contenido |
  |---|---|
  | `layer`, `kind`, `visible` | de la regla del YAML |
  | `source_entities` | `{tipo DXF: nº}` leídos en la capa |
  | `triangle_count` | triángulos de la capa, instancias incluidas |
  | `ground_check` | Si hay TIN: `checked`, `floating` (base sobre todo el terreno), `buried` (cubierta bajo todo el terreno), `intersecting` (el terreno entra entre base y cubierta) y `partially_buried` (supera la cubierta solo en parte de la huella). Categorías excluyentes, con tolerancia de 1 m. Las intersecciones con ladera son informativas y no recomiendan reasentar automáticamente. También disponible por capa en `report.json`. |
  | `objects` | arrays paralelos por objeto: `id` (identidad persistente, ver `objects.json`), `label` (E-001…), `height`, `floors`, `base`, `foundation_height`, `height_origin`, `source_type`, `handle`. En edificios adaptados al terreno, `base` es la cota de planta horizontal y `foundation_height` el desnivel máximo del zócalo bajo ella; no se suma a `height` ni a `floors`. Los handles de bloques con zócalo corresponden al INSERT original. |

- **Identificador de objeto:** el atributo de vértice `_OBJECT_ID` (float; redondéalo) es el
  índice en `extras.objects`. Permite seleccionar un edificio concreto dentro de la malla
  fusionada. En Three.js llega como `geometry.attributes._object_id`. Es un índice local del
  GLB: para guardar referencias usa `extras.objects.id[índice]`. En prototipos instanciados es
  la parte dentro de cada copia (`extras.object_ids[instancia][parte]`).
- **`_SURFACE`** (float): superficie semántica del vértice (0 genérica, 1 fachada exterior,
  2 fachada de patio, 3 cubierta, 4 zócalo, 5 suelo, 6 cara inferior). Ningún vértice se comparte
  entre superficies.
- **`TEXCOORD_0`** en **metros** en el GLB base: paredes = distancia sobre el contorno (continua
  en las esquinas) y cota absoluta; cubiertas y suelos = planta (x, y del dibujo, recentrados);
  mallas 3D de origen = por triángulo, en planta o vertical según su normal. En prototipos, metros
  locales del bloque. El GLB materializado las sustituye por UV de textura (ver abajo).
- **Instancing:** los bloques con al menos `instancing_min` copias idénticas (por defecto 10)
  van en nodos hijos `capa/BLOQUE` con `EXT_mesh_gpu_instancing` (TRANSLATION, ROTATION,
  SCALE). Su `extras` lleva `block`, `instances`, `objects_per_instance` y `object_ids` (por
  instancia, los IDs persistentes de sus objetos, en el orden de los atributos). Se excluyen de la
  instanciación, y se fusionan con el resto de la capa:
  - las copias reflejadas (determinante negativo) o con cizalla;
  - los bloques cuya geometría varía entre copias (p. ej. altura por atributo).

  `EXT_mesh_gpu_instancing` va en `extensionsUsed`, no en `extensionsRequired`.
- **Normales:** por vértice. Las mallas que vienen del DXF (3DFACE, MESH…) se desindexan para
  tener aristas vivas. En extrusiones, cada pared tiene sus propios vértices.
- **Materiales:** PBR metallic-roughness. El color del YAML (sRGB) se convierte a lineal en
  `baseColorFactor`. Con `opacity` < 1 se usa `alphaMode: BLEND`.

### Teselas (`--tiles <m>`)

Cada objeto va a la tesela de su centro XY (las instancias, a la de su traslación). Todas las
teselas comparten el mismo `origin_offset`, así que se cargan juntas sin desplazarlas.

`out/manifest.json`:
```json
{
  "schemaVersion": 1,
  "geometry_revision": "sha256:…",
  "origin_offset": [x, y, z],
  "units": "m",
  "crs": null,
  "center_lat": null,
  "center_lon": null,
  "tile_size": 500,
  "tiles": [{"id": "0_0", "file": "tiles/tile_0_0.glb", "bbox": {"min": [], "max": []},
             "triangles": 0, "size_bytes": 0}]
}
```

### Catálogo de objetos (`out/objects.json`)

Se escribe en cada conversión, también con teselas. Es la base de la materialidad
(docs/REQUISITOS_MATERIALIDAD_IA.md §5 y §9):

```json
{
  "schemaVersion": 1,
  "geometryRevision": "sha256:…",
  "units": "m", "sourceUnits": "mm", "crs": "EPSG:25830",
  "originOffset": [x, y, z],
  "north": {"axis": "+Y", "reference": "plan", "description": "…"},
  "surfaces": ["generic", "facade_exterior", "facade_courtyard", "roof", "foundation",
               "ground", "underside"],
  "objects": [{
    "id": "obj-1f3a…", "label": "E-001", "layer": "EDIFICIOS", "kind": "building",
    "sourceType": "LWPOLYLINE",
    "provenance": {"handles": ["2A"], "insert": null, "blockPaths": [], "attributes": {}},
    "heightM": 9.0, "floorM": null, "foundationHeightM": 0.0,
    "centerM": [x, y], "bboxM": {"min": [], "max": []}, "footprintAreaM2": 500.0,
    "surfaces": {"roof": {"triangles": 8, "areaM2": 500.0},
                 "facade_exterior": {"triangles": 8, "areaM2": 900.0}},
    "export": {"node": "EDIFICIOS", "objectIndex": 0}
  }]
}
```

- **`id`**: hash de la capa y de la procedencia DXF (handle; en bloques, INSERT de primer nivel,
  ruta de bloques y tipo). No depende del material, la tesela, la primitiva ni el orden. Los
  objetos que comparten procedencia (HATCH con varias islas) se desempatan por posición con
  `-2`, `-3`… Estable dentro de una revisión; tras editar el DWG hay que comprobarlo.
- **`label`**: nombre amigable por tipo (E edificio, V vial, A agua, G vegetación, T terreno,
  O otros), de norte a sur y de oeste a este. No es identidad.
- **`geometryRevision`**: hash de vértices (al micrómetro), caras y superficies de todos los
  objetos, líneas y CRS. Mismo archivo y configuración → misma revisión, también si el dibujo
  está en mm o en m.
- **Coordenadas** en metros y ejes del dibujo, **antes** del recentrado y del paso a Y-up: las
  referencias espaciales («al norte») se resuelven aquí. Norte = +Y del plano.
- **`surfaces`**: superficies presentes, etiquetadas al construir: fachada exterior, fachada de
  patio, cubierta, zócalo automático (del terreno a la cota de planta), suelo, tapa inferior o
  genérica (mallas 3D de origen). Fachadas, cubierta y zócalo solo existen en capas `building`;
  viales, agua y vegetación son una única superficie `ground` aunque tengan espesor.
- **`export`**: `{node, objectIndex}` en la malla fusionada o `{node, instance, part}` en un
  bloque instanciado. `null` con teselas: ahí la clave es el `id` en los `extras`.

### Plan de materialidad (`materials.json`)

Esquema estricto en `pipeline/src/extrude/materiality.py` (§9 de los requisitos). Contiene solo
asignaciones resueltas a IDs del catálogo, sus omisiones y, opcionalmente, la descripción
original:

```json
{
  "schemaVersion": 1, "projectId": "proyecto-a",
  "geometryRevision": "sha256:…", "materialRevision": "mat-003", "catalogVersion": "library-1",
  "assignments": [{
    "objectIds": ["obj-…"], "surface": "facade_exterior",
    "materialId": "brick_light", "materialVersion": "1",
    "parameters": {"tileSizeM": [2.0, 2.0], "rotationDeg": 0, "roughness": 0.85,
                   "color": "#d8c3a5"},
    "origin": "manual | ai_reviewed", "reason": "…"
  }],
  "omissions": [{"text": "Carpinterías negras", "reason": "El DWG no tiene carpinterías"}]
}
```

Se rechazan claves desconocidas, NaN/infinito, IDs con otro formato, superficies o materiales
inexistentes y URLs. `extrude validate-materials materials.json --objects out/objects.json`
comprueba además la revisión geométrica, que cada objeto tenga la superficie asignada, la versión
del material en la biblioteca y que ningún objeto + superficie tenga dos asignaciones.

### GLB materializado (`extrude materialize`)

```sh
uv run extrude materialize out/model.glb materials.json            # → out/model.materialized.glb
uv run extrude materialize out/model.glb materials.json -o vista.glb --compress meshopt
uv run extrude library                                            # IDs y versiones de material
```

Parte del `model.glb` **sin comprimir** de `convert`, sin volver a leer el DWG. El plan se valida
contra el propio GLB (revisión geométrica, IDs, superficies) y la biblioteca; si algo no cuadra no
se escribe nada. La salida se escribe a un temporal y se renombra al final.

- **Primitivas**: una por capa y material. Las superficies sin asignación conservan el material
  de su capa. `POSITION`, `NORMAL`, `_OBJECT_ID`, `_SURFACE`, los nodos y sus `extras` se copian
  sin cambios: la geometría es la misma (solo se reparten vértices).
- **Instancias**: las copias se reagrupan por asignación. Si cambias el material de una copia,
  sale a otro nodo `capa/BLOQUE` con sus propias `TRANSLATION/ROTATION/SCALE` y su subconjunto
  de `extras.object_ids`; las demás no cambian. Con textura, las copias de distinta escala también
  se separan para que el ladrillo mida lo mismo en todas.
- **UV de textura**: `TEXCOORD_0` = metros × escala de la copia, girados `rotationDeg` y divididos
  por `tileSizeM`, con v invertida (convención glTF). La escala física va en las UV, no en
  `KHR_texture_transform`: Three.js, Blender y cualquier visor muestran lo mismo.
- **Materiales**: PBR metallic-roughness. Con textura, `baseColorTexture` es un PNG de
  luminancia y `baseColorFactor` el color del preset (o `parameters.color`) compensado por la
  luminancia media. `extras`: `material_id`, `material_version`, `color`, `material_revision`.
  Vidrio (transparencia, sin refracción) y agua no llevan textura.
- **Texturas**: procedurales de Extrude (512 px, sin licencias de terceros), incrustadas en el
  binario (`images[].bufferView`): el GLB se abre sin conexión.
- **`scene.extras`** añade `material_revision`, `material_project`, `material_catalog`,
  `material_assignments` y `uv_convention`. Un GLB materializado no se vuelve a materializar:
  cada plan se aplica sobre el `model.glb` base.
- Válido en el validador de Khronos sin errores ni avisos (test automático).

### Compresión (`--compress draco|meshopt`)

Se aplica con `gltf-transform` 4.5.1 (carpeta `tools/`) usando cuantización de 16 bits para
posiciones y para `_OBJECT_ID`:

- **IDs:** los `_OBJECT_ID` enteros hasta 65 535 por primitiva se recuperan exactos al
  redondear. Verificado en tests decodificando ambos formatos. Con más objetos por capa,
  usa `--tiles`.
- **Precisión de posiciones:** el error es ≈ tamaño del bbox / 65 535. Unos 0,3 mm en la
  muestra de 150 m; unos 7 cm en una escena de 5 km. Para escenas grandes, combina con `--tiles`.
- **Qué se conserva:** `extras`, `EXT_mesh_gpu_instancing` y `_OBJECT_ID`. Verificado tras
  comprimir y decodificar.
- **Estructura:** Meshopt puede mover la malla a un nodo hijo sin nombre para guardar la
  descuantización. Los `extras` siguen en el nodo de la capa, así que hay que buscarlos en
  los ancestros.
- **Visor:** el de Extrude decodifica ambos formatos (Draco servido localmente desde
  `public/draco/`).

### Validación

`just validate out/model.glb` (validador oficial de Khronos 2.0.0-dev.3.10). Todas las muestras,
teselas y versiones comprimidas validan **sin errores ni avisos** (test automático).

## Vistas (`SceneView`)

Una vista es una instantánea del estado del visor (`viewer/src/views/viewState.ts`):

| Campo | Contenido |
|---|---|
| `camera` | `type` (perspective/orthographic), `position`, `target`, `up` (coordenadas de la escena), `fovDeg`, `orthoSize`, `near`, `far` |
| `render` | `width`, `height`, `aspect` ("16:9") |
| `sun` | `mode`, `isoDate` (hora local con desfase), `lat`, `lon`, `azimuthDeg` (desde el norte, horario), `elevationDeg`, `northRotationDeg`, `intensity`, `colorTempK` |
| `ambient` | `intensity`, `background` (sky/hdri/color), `hdri` (nombre de archivo), `hdriRotationDeg`, `hdriIntensity`, colores |
| `shadows`, `ground`, `tonemapping` | ajustes de sombras, suelo y tone mapping (`type`, `exposure`) |
| `lights` | `type` (point/spot/area), `position`, `target`, `color`, `intensity`, `distance`, `angleDeg`, `width`, `height` |
| `layers` | `{capa: {visible, colorOverride?}}` |
| `thumbnailDataUrl` | miniatura JPEG (solo se aceptan URLs `data:image/…` al importar) |

**`fovDeg` y `orthoSize` son los del render final**: lo que encierra el "marco de cámara" a la
resolución elegida. Al restaurar, el visor recalcula su propio FOV para que el marco muestre
exactamente ese encuadre. El PNG, el GLB y Blender usan ese mismo encuadre.

Las vistas se guardan por modelo (nombre del GLB) en IndexedDB del navegador. Para conservarlas
o compartirlas, exporta `views.json`.

## `views.json`

```json
{
  "schemaVersion": 1,
  "generator": "extrude-viewer 0.1.0",
  "coordinateSystem": {
    "units": "m", "up": "Y",
    "axisTransform": "(x, y, z)_dwg -> (x, z, -y)_gltf",
    "originOffset": [x, y, z], "crs": "EPSG:25830" | null
  },
  "model": { "file": "model.glb" },
  "views": [ { ...SceneView, "cameraWorld": { "position": [..], "target": [..] } } ]
}
```

- Las coordenadas de cada vista están en el **sistema de la escena exportada** (Y-up, recentrado).
- `cameraWorld` repite cámara y objetivo en **coordenadas reales del DWG** (Z-up, p. ej. UTM):
  `x = x + o[0]`, `y = -z + o[1]`, `z = y + o[2]`.
- Al importar se valida la estructura. Los errores dicen qué campo falla. Las secciones que
  falten se rellenan con valores por defecto. Si el `originOffset` no coincide con el del modelo
  cargado, se avisa.

## `blender_setup.py`

Disponible para modelos de un solo GLB. En modelos teselados el visor bloquea esta exportación:
exporta `views.json` o convierte sin `--tiles` para generar el script de Blender.


Script autocontenido (los datos de las vistas van en Base64 dentro del propio script).

```sh
# En Blender: Scripting → Abrir → Run Script (con el GLB en la misma carpeta)
# O sin interfaz:
blender -b -P blender_setup.py -- [--glb modelo.glb] [--render-all renders/] [--percentage 50] [--report informe.json]
```

Qué hace:
1. Si la escena es la de fábrica (Cube, Light, Camera y nada más), la vacía. Si no, no toca nada.
2. Importa el GLB con `bpy.ops.import_scene.gltf`.
3. Por cada vista crea, en la colección `Extrude vistas/N. nombre`:
   - una cámara con la lente y el modo ortográfico de la vista;
   - una luz Sun;
   - sus luces puntuales, focos y de área.
4. `apply_view("nombre")` activa la cámara, ajusta la resolución, enciende solo las luces de esa
   vista, oculta (`hide_render`) las capas ocultas, y aplica el mundo y la gestión de color.
5. `render_all("//renders")` renderiza todas las vistas con el motor activo de la escena.

Conversiones (calculadas en `viewer/src/export/blenderScript.ts` y cubiertas por tests):

| Concepto | Visor (Three.js) | Blender |
|---|---|---|
| Ejes | escena Y-up | `(x, y, z) → (x, −z, y)` (lo mismo que hace el importador glTF) |
| Orientación de la cámara | `lookAt` de Three.js | el cuaternión se calcula en el visor y se pasa tal cual, así el giro coincide también en plantas |
| FOV | vertical, en grados | `sensor_fit = VERTICAL`, sensor de 24 mm, `lens = 12 / tan(fov/2)` |
| Ortográfica | `orthoSize` (media altura) | `ortho_scale = 2 × orthoSize` |
| Sol | intensidad × factor de crepúsculo | Strength [W/m²] = mismo número; ángulo 0,53° |
| Puntual / foco | candelas | W = cd × 4π; `spot_size` = 2 × ángulo, `spot_blend` = 0,3 |
| Área | luminancia (nits) | W = nits × ancho × alto × π |
| Cielo procedural | entorno PMREM × 0,5 | mundo de color cielo, Strength = luz ambiente × 0,5 (**aproximado**) |
| HDRI | archivo cargado en el visor | Environment Texture con el archivo de la carpeta del script; rotación en Z (**sentido no verificado visualmente**) |
| Tone mapping | AgX / Neutral / ACES / ninguno | AgX / Khronos PBR Neutral / Filmic (aproximación a ACES) / Standard; exposición = log₂(exposure) |

Las potencias son **aproximadas**: conservan las proporciones entre luces del visor, con una
escala global elegida para que el sol del visor (intensidad 3) dé Strength 3. No es una
conversión física absoluta.

## GLB con cámaras y luces

Se exporta desde el panel de vistas ("GLB + cámaras y luces") y se llama `<modelo>_vistas.glb`.

- Es el GLB original con **solo el JSON modificado**: el bloque binario no se toca, así que se
  conservan la compresión, los `extras` y `_OBJECT_ID`.
- **Cámaras:** una por vista (`perspective` con `yfov` y `aspectRatio`, u `orthographic`).
- **Luces:** las de la vista activa (o la primera) con `KHR_lights_punctual`: el sol como
  `directional`, más `point` y `spot`.
  - **Las luces de área no existen en `KHR_lights_punctual`:** se omiten y el visor lo avisa.
  - Las intensidades están en las unidades físicas de glTF (lux y candelas), las mismas que usa
    Three.js.
- Todo cuelga de un nodo "Extrude: vistas y luces" en la escena. Validado con gltf-validator:
  0 errores y 0 avisos.

## PNG

Render de la vista actual a la resolución del panel (p. ej. 3840×2160), con el encuadre exacto
del marco de cámara. Se redimensiona el lienzo WebGL solo durante ese render. El límite lo pone
la GPU (`MAX_VIEWPORT_DIMS`): si se supera, el mensaje indica el máximo.

## Compatibilidad con herramientas externas

Probado en esta máquina con Blender 5.3.0 (Windows):

| Herramienta | GLB del pipeline | Instancing | Draco | Meshopt | `extras` | GLB con cámaras y luces | `blender_setup.py` |
|---|---|---|---|---|---|---|---|
| Visor Extrude (Three.js 0.187) | verificado | verificado | verificado | verificado | verificado | — | — |
| Blender 5.3.0 | verificado | verificado (cada copia se expande a un objeto) | verificado | verificado | verificado (propiedades personalizadas) | verificado: 2 cámaras y el sol | verificado en modo sin ventana: cámaras, soles, luces, visibilidad por vista y render de todas las vistas |
| Twinmotion, D5, Unreal, Enscape | no verificado | no verificado | no verificado | no verificado | no verificado | no verificado | — |

No se afirma compatibilidad con herramientas que no se han probado. Si una herramienta no admite
instancing o compresión, convierte con `instancing_min: 0` y sin `--compress`.
