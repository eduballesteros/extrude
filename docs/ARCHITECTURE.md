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

## Suposiciones (Fase 0, sin respuesta a las preguntas iniciales)

Se revisarán cuando llegue el DWG real.

| Tema | Suposición |
|---|---|
| Versión DWG | AutoCAD 2018 (AC1032). Se soporta DXF R2010–R2018+. |
| Contenido | Mayoritariamente 2D (polilíneas por capa), con algo de 3DFACE/MESH; los sólidos ACIS se reportan como no soportados. |
| Escala | Barrio (miles de edificios); se diseña para ciudad (~50 000 edificios) con fusión por capa e instancing. |
| Alturas | Dependen de cada proyecto: se configuran por capa en YAML (`init-config` propone un borrador). |
| Destino de render | Blender (prioritario). Otros motores vía glTF, marcados como "no verificado". |
| Conversor DWG | No instalado en el entorno de desarrollo: se trabaja con DXF; ODA/LibreDWG solo se prueban con dobles (mocks). |
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
- **Herramientas Node** (`tools/`): validador de Khronos y `gltf-transform` se ejecutan con `node`
  directamente (nunca con `npx.cmd` ni shell), con rutas generadas por Extrude.
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

`suncalc` y `lil-gui` se añadirán en la Fase 4, cuando se usen.
TypeScript se fija en 6.0.x porque `typescript-eslint` 8.x aún no admite TS 7.
