# Arquitectura de Extrude

## Visión general

```
 DWG/DXF ──► converters ──► dxf_reader ──► topology ──► extrusion/meshes3d ──► gltf_export ──► model.glb
              (ODA,         (modelo        (lazos,      (2D→3D, 3DFACE,       (recentrado,     + report.json/md
               LibreDWG,     intermedio     huecos,      MESH, instancias)     Y-up, extras)
               passthrough)  en WCS)        validación)
                                                                                  │
                                                                                  ▼
                                                          viewer (Three.js): luces, sol, vistas
                                                                                  │
                                                         views.json · blender_setup.py · GLB+luces · PNG
```

## Alcance y convenciones

Estas convenciones describen el pipeline. Las alturas, la clasificación de capas y las
cotas válidas dependen de la configuración de cada proyecto.

| Tema | Suposición |
|---|---|
| Versión DWG | AutoCAD 2018 (AC1032). Se soporta DXF R2010–R2018+. |
| Contenido | Mayoritariamente 2D (polilíneas por capa), con algo de 3DFACE/MESH; los sólidos ACIS se reportan como no soportados. |
| Escala | Barrio (miles de edificios); se diseña para ciudad (~50 000 edificios) con fusión por capa e instancing. |
| Alturas | Dependen de cada proyecto: se configuran por capa en YAML (`init-config` propone un borrador). |
| Destino de render | Blender (prioritario). Otros motores vía glTF, marcados como "no verificado". |
| Conversor DWG | DXF se lee directamente; DWG requiere ODA o LibreDWG. Las pruebas automáticas de adaptadores usan dobles y la disponibilidad real se consulta con `extrude doctor`. |
| Python | `requires-python >= 3.12`; desarrollado y probado con 3.13.2. |
| Tareas | `just` en lugar de `make` (no hay `make` nativo en Windows; `just` funciona igual en Linux/macOS). |

## Decisiones

- **Conversión DWG→DXF** (`converters/`): clase abstracta `DwgConverter` con adaptadores ODA y
  LibreDWG, elegidos por disponibilidad (ODA primero). El passthrough de `.dxf` vive en
  `ensure_dxf()` en lugar de en una clase propia: no lanza procesos ni necesita estado.
- **Seguridad de la conversión**: el DWG se considera no confiable. Se valida la cabecera
  (`AC####`), se copia a un directorio temporal con nombre fijo (`input.dwg`) y se ejecuta el
  conversor sin shell, con lista de argumentos y timeout (`CONVERSION_TIMEOUT_S`). El nombre
  original del archivo nunca llega a la línea de comandos.
- **Errores accionables**: toda excepción de usuario hereda de `ExtrudeError(problem, fix)`.
- **Lector DXF** (`dxf_reader.py`): `ezdxf.recover` para tolerar DXF con defectos (los
  problemas reparados o no reparables pasan a avisos). Curvas vía `ezdxf.path` (resuelve OCS,
  bulges, arcos, splines) aplanadas con `DEFAULT_FLATTEN_TOLERANCE_M`. INSERT/MINSERT vía
  `virtual_entities()` de forma recursiva; la capa "0" dentro de un bloque hereda la capa del
  INSERT (ezdxf no lo hace). Los ATTRIB se emiten como textos con `tag` y además se propagan a la
  geometría del bloque (`Provenance.attributes`).
- **Contabilidad**: cada entidad visitada se cuenta en `read`; si no se convierte acaba en
  `unsupported` (ACIS), `skipped` (DIMENSION, VIEWPORT, …) o `failed` (excepción al leerla, con
  aviso). Una entidad corrupta no aborta la lectura.
- **Configuración** (`config.py`): pydantic con `extra=forbid` (una clave mal escrita es un error,
  no se ignora). `init-config` genera un borrador por proyecto; ver docs/CONFIG.md.
- **Topología** (`topology.py`): en metros. Extremos de LINE/ARC/polilíneas abiertas unidos por
  clustering (KD-tree + union-find) dentro de `tolerance`, luego `polygonize_full`. Huecos por
  contención par/impar con STRtree. Todo lo reparado o descartado se cuenta en `TopologyStats`.
- **Extrusión** (`extrusion.py`): earcut para tapas con huecos; paredes con vértices propios
  (sombreado plano). La orientación de cada triángulo se corrige por su área con signo, sin
  depender del orden de salida de earcut.
- **Export GLB** (`gltf_export.py`): escrito a mano (cabecera + chunk JSON + chunk BIN) en lugar
  de `pygltflib`: el formato es pequeño y así controlamos atributos personalizados (`_OBJECT_ID`)
  y extensiones sin pelear con el modelo de datos de la librería. Una dependencia menos.
- **Instancing** (`instancing.py`): el lector anota en cada entidad el INSERT de primer nivel
  (handle, bloque, matriz). Una copia se instancia solo si, llevada a coordenadas del bloque, su
  geometría coincide con el prototipo; si no, se fusiona. Así nunca se instancia algo que depende
  del INSERT (alturas por atributo, contornos fusionados).
- **Identidad y superficies** (`catalog.py`, `surfaces.py`): cada triángulo lleva su superficie
  semántica desde que se crea (cubierta, fachada exterior/patio, zócalo, suelo…), sin deducirla
  luego por la normal. El zócalo se separa de la fachada en la cota de planta con dos quads
  coplanares, sin cambiar la envolvente. Al terminar `build_scene`, cada objeto recibe un ID
  persistente derivado de su procedencia DXF y una etiqueta (E-001…); `convert` escribe
  `objects.json` con la revisión geométrica (hash de la geometría construida).
- **Plan de materialidad** (`materiality.py`, `material_library.py`): `materials.json` es un
  documento versionado e independiente del prompt, validado con pydantic estricto y contra el
  catálogo y la biblioteca. Los 12 presets aún no tienen texturas (licencias pendientes).
- **Materialización** (`materialize.py`, `textures.py`): el GLB base ya lleva superficie y UV en
  metros por vértice, así que aplicar un plan es reescribir el GLB (≈1 s con miles de edificios)
  sin volver a leer el DWG (RN-01). Las UV en metros se generan donde se construyen las paredes
  (distancia sobre el contorno, continua en las esquinas); la escala física y el giro se escriben
  en las UV al materializar, no con `KHR_texture_transform`, para que cualquier motor vea lo
  mismo. Los prototipos instanciados guardan UV en metros locales y las copias se reagrupan por
  material y escala. Texturas procedurales propias (sin licencias de terceros) y en escala de
  grises: el color es el factor del material.
- **Materiales en el visor** (`viewer/src/materials`, `ui/materialsPanel.ts`,
  `materials_service.py`): el visor no materializa por su cuenta. Edita un borrador de plan y lo
  envía a `extrude serve`, que lo valida y aplica con el mismo código que la CLI; previsualizar
  y confirmar recargan el GLB del servidor conservando cámara, sol y capas. El catálogo de objetos
  se lee del propio GLB (`_OBJECT_ID`, `_SURFACE`, `extras`). Confirmar crea una revisión
  inmutable (`materials/mat-00N.json`) numerada por el servidor.
- **Edición con IA** (`ai_planner.py`, `ai_providers.py`, `edit_tools.py`, `glb_edit.py`,
  `project_edit.py`, `project_state.py`; visor: `materials/ai.ts`, `materials/viewerOps.ts`,
  `ui/editSection.ts`): la IA solo interpreta y planifica con las herramientas del registro
  (`edit_tools.TOOLS`, contratos explícitos; lo que falta está en `PLANNED`). El servidor
  construye el contexto con el estado actual del proyecto (referencias cortas, posiciones
  relativas, TRS completo de las copias de bloque), hace una única llamada (SDK oficiales con
  `max_retries=0` tras la interfaz `Provider`), valida el plan y resuelve en código selectores,
  compatibilidades, conflictos y prioridades. Aplicar es una transacción sobre una copia del GLB
  (`GlbEditor`: reescribe accesores y compacta el binario) más el plan de materiales; confirmar
  crea una revisión del proyecto (`project.json`) que deshacer/rehacer recorren. Las vistas y el
  sol los ejecuta el visor. Detalles en [EDICION_IA.md](EDICION_IA.md).
- **Herramientas Node** (`tools/`): validador de Khronos y `gltf-transform` se ejecutan con `node`
  directamente (nunca con `npx.cmd` ni shell), con rutas generadas por Extrude.
- **Visor**: el estado (`viewer/src/state.ts`) es un objeto JSON plano. Los módulos de escena lo
  aplican (`App.applyX()`) y la interfaz lo modifica, así que una vista de la Fase 5 es una copia de ese estado.
- **Sol**: `suncalc` mide el azimut desde el sur y positivo al oeste; se convierte a la
  convención geográfica (desde el norte, sentido horario). Los tests lo comparan con referencias
  astronómicas independientes (elevación en el mediodía solar de los solsticios, salida del sol
  por el este en el equinoccio), no solo con el propio suncalc.
- **Sombras**: en lugar de CSM, un único shadow map ajustado a la caja de la escena o, si su
  diagonal pasa de 1,5 km, a la zona visible alrededor del objetivo, redondeado a la rejilla de texels
  para que no tiemble. Es más simple que CSM (que exige parchear los materiales) y en la ciudad de
  prueba da 60 FPS. `PCFSoftShadowMap` ya no existe en Three.js 0.187: se usa PCF con radio, o VSM.
- **Entorno**: el cielo procedural genera el mapa de entorno (PMREM, regenerado como mucho cada
  200 ms al mover el sol) escalado ×0,5. La luz hemisférica solo se usa con fondo de color liso,
  para no sumar dos veces la luz ambiente.
- **Vistas** (`viewer/src/views`): una vista guarda el FOV y el `orthoSize` del **render final**
  (lo que encierra el marco de cámara), así que el PNG, el GLB y Blender coinciden. El visor adapta
  su FOV al restaurar. Persistencia en IndexedDB por modelo; `views.json` validado al importar
  (entrada no confiable: solo miniaturas `data:image/`).
- **Exportaciones** (`viewer/src/export`): el GLB con cámaras y luces se obtiene parcheando el JSON
  del GLB original, con el bloque binario intacto. `blender_setup.py` lleva los datos en Base64 (un
  nombre con `'''` no puede inyectar código) y las orientaciones ya calculadas con el `lookAt`
  de Three.js, para que el giro de la cámara coincida con el visor. Acepta `--report`, que usa el test
  de integración que ejecuta Blender en modo sin ventana.
- **Interfaz** (`viewer/src/ui`): componentes propios en TypeScript y CSS (`styles.css`, variables
  CSS y `backdrop-filter`), sin framework ni lil-gui. Todo texto procedente del usuario o del DWG se
  asigna con `textContent`; `innerHTML` solo se usa con SVG constantes (`icons.ts`). La lógica
  testeable (cubo de orientación, amanecer y ocaso, progreso de descarga) vive fuera del DOM.
- **Coordenadas**: el lector trabaja en WCS (float64). El recentrado y el paso Z-up → Y-up
  `(x, y, z)_dwg → (x, z, -y)_gltf` se hacen solo al exportar.

## Dependencias y justificación

### Pipeline (Python)

| Paquete | Para qué |
|---|---|
| ezdxf | Lectura DXF, OCS/WCS, explosión de bloques y generación de fixtures |
| shapely | Validación y reparación de polígonos, `polygonize`, unión, contención (huecos) |
| numpy | Álgebra de vértices en float64 |
| mapbox-earcut | Triangulación de polígonos con huecos (rápida, wheels para todas las plataformas) |
| scipy | `Delaunay` para TIN de terreno |
| trimesh | Validación de mallas (`is_watertight`, volumen, normales) |
| pydantic | Esquema y validación de la configuración YAML |
| pyyaml | Lectura del YAML |
| typer | CLI |
| *dev:* pytest, ruff, mypy, types-pyyaml | Tests, lint/formato, tipado estricto |

### Visor (TypeScript)

| Paquete | Para qué |
|---|---|
| three | Render WebGL, loaders glTF/HDR/EXR, controles |
| *dev:* vite, typescript, vitest, eslint, typescript-eslint, prettier | Build, tipado, tests, lint/formato |

### Herramientas (`tools/`, Node)

| Paquete | Para qué |
|---|---|
| gltf-validator | Validación oficial de Khronos (tests y `just validate`) |
| @gltf-transform/cli | Compresión Draco/Meshopt opcional (`--compress`) |

### Visor: dependencias de la Fase 4

| Paquete | Para qué |
|---|---|
| suncalc 1.9.0 | Posición solar (la versión clásica, la que encaja con `@types/suncalc` 1.9.2) |
| @types/node (dev) | Tipos para los tests de integración (Blender, py_compile, gltf-validator) |

TypeScript se fija en 6.0.x porque `typescript-eslint` 8.x aún no admite TS 7.

## Ampliación: edición arquitectónica, maestro editable y modo compartido

- **Herramientas nuevas** (`edit_tools.py`, ejecutadas en `arch_ops.py`, `glb_build.py` y
  `site_assets.py`): entorno instanciado, ventanas y balcones como elementos alojados (`host`,
  `facade`), retranqueos con `shapely`, copias independizadas con su TRS horneado, muros y
  cumbreras de cubiertas inclinadas. Cada herramienta tiene una comprobación de compatibilidad
  por objeto. La ejecución es transaccional (todo o nada) y se valida con `geometry_check`.
- **Números estables de copia** (`extras.numbers`): quitar o independizar una copia no renumera
  las demás, así que las etiquetas «BLOQUE #n.p» identifican siempre la misma copia.
- **Maestro editable** (`master.py`): toda edición trabaja sobre un GLB sin comprimir y de una
  pieza (`model.glb` o `model.master.glb`). Teselas, manifest y exportaciones comprimidas se
  derivan de él en cada revisión (`ProjectState._retile`).
- **Modo compartido** (`access.py`, `extrude serve --shared`): cuentas con scrypt, sesiones con
  token Bearer en memoria, `access.json` por trabajo (dueño, editor, lector; sin permiso = 404),
  revisión base obligatoria en confirmaciones y deshacer, y límites por usuario. No cambia la
  interfaz de red.
