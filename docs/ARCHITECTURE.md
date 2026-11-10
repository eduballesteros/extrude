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
| Alturas | Texto dentro del polígono con regex por defecto `(\d+)\s*P` (nº de plantas), altura por planta 3,0 m. |
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
| pygltflib | Escritura GLB con control total de `extras`, nodos e instancing |
| pydantic | Esquema y validación de la configuración YAML |
| pyyaml | Lectura del YAML |
| typer | CLI |
| *dev:* pytest, ruff, mypy, types-pyyaml | Tests, lint/formato, tipado estricto |

### Visor (TypeScript)

| Paquete | Para qué |
|---|---|
| three | Render WebGL, loaders glTF/HDR/EXR, controles |
| *dev:* vite, typescript, vitest, eslint, typescript-eslint, prettier | Build, tipado, tests, lint/formato |

`suncalc` y `lil-gui` se añadirán en la Fase 4, cuando se usen.
TypeScript se fija en 6.0.x porque `typescript-eslint` 8.x aún no admite TS 7.
