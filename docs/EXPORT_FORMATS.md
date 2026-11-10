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
| `crs` | lo indicado en `crs` del YAML (p. ej. `"EPSG:25830"`) o `null`. No se reproyecta nada. |
| `axis_transform` | `"(x, y, z)_dwg -> (x, z, -y)_gltf"` |
| `bbox` | `{min, max}` en coordenadas glTF (ya recentradas) |
| `generator` | versión de Extrude |

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
  | `objects` | arrays paralelos por objeto: `height`, `height_origin`, `source_type`, `handle` |

- **Identificador de objeto:** el atributo de vértice `_OBJECT_ID` (float; redondéalo) es el
  índice en `extras.objects`. Permite seleccionar un edificio concreto dentro de la malla
  fusionada. En Three.js llega como `geometry.attributes._object_id`.
- **Instancing:** los bloques con al menos `instancing_min` copias idénticas (por defecto 10)
  van en nodos hijos `capa/BLOQUE` con `EXT_mesh_gpu_instancing` (TRANSLATION, ROTATION,
  SCALE). Su `extras` lleva `block`, `instances` y `objects_per_instance`. Se excluyen de la
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
  "origin_offset": [x, y, z],
  "units": "m",
  "crs": null,
  "tile_size": 500,
  "tiles": [{"id": "0_0", "file": "tiles/tile_0_0.glb", "bbox": {"min": [], "max": []},
             "triangles": 0, "size_bytes": 0}]
}
```

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

## Compatibilidad con herramientas externas

| Herramienta | GLB básico | `EXT_mesh_gpu_instancing` | Draco / Meshopt | `extras` |
|---|---|---|---|---|
| Visor Extrude (Three.js 0.187) | verificado | verificado | verificado | verificado |
| Blender | no verificado (se verificará en Fase 5) | no verificado | no verificado | no verificado |
| Twinmotion, D5, Unreal, Enscape | no verificado | no verificado | no verificado | no verificado |

No se afirma compatibilidad con ninguna herramienta sin haberla probado. Si una herramienta no
admite instancing o compresión, convierte con `instancing_min: 0` y sin `--compress`.
