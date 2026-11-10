# Extrude

Convierte planos DWG/DXF urbanos (proyecto, manzana, barrio o ciudad) en modelos 3D glTF/GLB,
y los previsualiza en un visor web con control de sol, luces y vistas exportables a Blender.

> Estado: **Fase 5** (vistas con miniaturas y exportación a views.json, Blender, GLB con cámaras y luces, y PNG). Ver [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Requisitos

| Herramienta | Versión | Instalación |
|---|---|---|
| Python | ≥ 3.12 (probado con 3.13) | <https://www.python.org> o `pyenv` |
| uv | ≥ 0.4 | `pip install uv` o <https://docs.astral.sh/uv/> |
| Node.js | ≥ 22.12 | <https://nodejs.org> |
| Blender (opcional) | ≥ 4.2; probado con 5.3 | `winget install BlenderFoundation.Blender`. Lo usan los tests de `blender_setup.py`; si está en otra ruta, defínela en `EXTRUDE_BLENDER_PATH` |
| just | ≥ 1.38 | `uv tool install rust-just`, `winget install Casey.Just`, `brew install just` |

Las herramientas Node de `tools/` (validador glTF y `gltf-transform` para `--compress`) se
instalan con `just setup-tools`.

### Conversor DWG → DXF (opcional, uno de estos)

Sin conversor, Extrude solo acepta `.dxf`. Comprueba lo que tienes con `just doctor`.

- **ODA File Converter** (recomendado; gratuito con registro): descárgalo de
  <https://www.opendesign.com/guestfiles/oda_file_converter>.
  - Windows: instalador `.msi`; se detecta en `C:\Program Files\ODA\ODAFileConverter*\`.
  - Linux: paquete `.deb`/`.rpm`; se detecta en `/usr/bin/ODAFileConverter`
    (en servidores sin pantalla puede necesitar `xvfb-run`).
  - macOS: `.dmg`; se detecta en `/Applications/ODAFileConverter.app`.
  - Ruta personalizada: variable de entorno `EXTRUDE_ODA_PATH`.
- **LibreDWG** (`dwg2dxf`, open source; soporte parcial de DWG recientes):
  - Debian/Ubuntu: `sudo apt install libredwg-tools`
  - macOS: `brew install libredwg`
  - Windows: binarios en <https://github.com/LibreDWG/libredwg/releases>
  - Ruta personalizada: `EXTRUDE_DWG2DXF_PATH`.
- **Manual**: en AutoCAD/BricsCAD, `SAVEAS` → DXF (versión 2018 o 2013).

## Puesta en marcha

```sh
just setup     # instala dependencias de pipeline y visor
just check     # lint + tests
just doctor    # conversores DWG→DXF disponibles
just dev       # visor en http://localhost:5173
```

## Inspeccionar un plano antes de convertir

`inspect` lee el dibujo sin generar 3D y muestra lo necesario para escribir el YAML de capas:
unidades, bbox, entidades por tipo y por capa, ejemplos de textos (para el regex de plantas),
etiquetas de atributos de bloque, entidades no soportadas y avisos.

```sh
cd pipeline
uv run extrude inspect ../samples/block_2d.dxf
uv run extrude inspect ../samples/nested_blocks.dxf --json
uv run extrude inspect plano.dxf --units mm      # si $INSUNITS falta o es incorrecto
```

Muestras sintéticas en `samples/` (se regeneran con `just samples`):

| Archivo | Contenido |
|---|---|
| `block_2d.dxf` | Manzana 2D: edificios con textos "nP", patio interior, borde curvo, vial y agua |
| `block_2d_utm.dxf` | La misma manzana desplazada a coordenadas UTM (230000, 4140000) |
| `nested_blocks.dxf` | Bloques anidados repetidos con rotación, escala, OCS invertido y atributo `PLANTAS` |
| `faces_3d.dxf` | 3DFACE, MESH, polyface, SOLID 2D y sólidos ACIS (no soportados), en milímetros |
| `dirty.dxf` | Polilínea casi cerrada, LINE/ARC sueltas, duplicados, pajarita, área nula, SPLINE, HATCH con hueco |

## Configuración por proyecto

Las alturas y el significado de cada capa dependen del proyecto, así que se declaran en un YAML.
`init-config` genera un borrador a partir del propio plano (detecta atributos de plantas, textos
tipo `4P`/`B+3`/`IV`/`12.5m` o cotas Z) para revisarlo y ajustarlo:

```sh
cd pipeline
uv run extrude init-config ../samples/block_2d.dxf -o ../proyecto.yaml
```

Referencia completa: [docs/CONFIG.md](docs/CONFIG.md). Ejemplo comentado: `pipeline/config/example.yaml`.

## Convertir

```sh
cd pipeline
uv run extrude convert plano.dwg -c proyecto.yaml -o ../out/proyecto
uv run extrude convert plano.dxf -c proyecto.yaml -o ../out/ciudad --tiles 500 --compress meshopt
```

Genera `model.glb` (o `tiles/*.glb` + `manifest.json`), `report.md` y `report.json`. El informe
lista todo lo leído, convertido, ignorado, reparado o descartado, con su capa. Formato del GLB:
[docs/EXPORT_FORMATS.md](docs/EXPORT_FORMATS.md).

Con las muestras: `just convert-samples` y `just validate out/block_2d/model.glb`.

## Visor

```sh
just dev                # http://localhost:5173
```

Para abrir un modelo:
- arrastra un `.glb` a la ventana;
- usa el selector de archivo;
- o abre `http://localhost:5173/?model=/models/x.glb`, con el archivo en `viewer/public/models/`.

Añade `&debug` a la URL para tener la app en `window.__extrude` desde la consola.

| Función | Cómo |
|---|---|
| Navegar | Órbita con el ratón. Vuelo: botón "Vuelo", luego WASD, Q/E para bajar/subir, Shift para ir rápido y Esc para salir. Proyección perspectiva u ortográfica, FOV ajustable. |
| Centrar | Doble clic sobre un objeto |
| Información | Clic sobre un objeto: capa, altura, de dónde sale la altura, tipo DXF, handle, bloque e instancia |
| Capas | Panel "Capas": visible, color, aislar, mostrar todas |
| Sol | Por fecha, hora local, UTC±, latitud y longitud (suncalc) o manual (azimut y elevación). También rotación del norte, intensidad, temperatura de color y animación del día |
| Sombras | Ajuste automático a toda la escena o, si mide más de 1,5 km, a la zona visible. Resolución, filtro PCF o VSM, suavizado, bias y normal bias |
| Entorno | Cielo procedural, HDRI (`.hdr`/`.exr`) con rotación e intensidad, o color liso. Suelo opcional que recibe sombras |
| Luces | Puntuales, focos y de área: se colocan con un clic en la escena y se mueven con un gizmo. Se editan desde el panel |
| Render | Tone mapping AgX, ACES, Neutral o ninguno; exposición |
| Rendimiento | Abajo a la izquierda: FPS, triángulos y draw calls |

### Vistas y exportación

El panel **Vistas** (a la izquierda) permite:
- guardar el estado actual (cámara, sol, luces, capas, entorno) con miniatura;
- restaurar con transición de cámara, renombrar, actualizar, duplicar, reordenar y borrar;
- elegir la resolución de render y ver el **marco de cámara** con su encuadre exacto.

Las vistas se guardan en el navegador (IndexedDB) por modelo. Exportaciones:

| Archivo | Para qué |
|---|---|
| `views.json` | Todas las vistas, con `originOffset` y la cámara en coordenadas reales del DWG |
| `blender_setup.py` | Cópialo junto al GLB y ejecútalo en Blender: crea cámaras, sol, luces y visibilidad por vista. `render_all()` renderiza todas |
| `<modelo>_vistas.glb` | El GLB con cámaras de todas las vistas y luces de la activa (`KHR_lights_punctual`) |
| `PNG` | Render de la vista actual a la resolución elegida |

Detalles, conversiones y compatibilidad verificada: [docs/EXPORT_FORMATS.md](docs/EXPORT_FORMATS.md).

Convenio de ejes: el +Y del dibujo es el −Z de la escena. "Norte (° horario)" gira el norte
real respecto al +Y del dibujo.
## Rendimiento medido

Ciudad sintética (`just bench`): 50 000 edificios con rótulo de plantas y 12 500 árboles, en UTM.
Medido en Windows 11 con Python 3.13 y una RTX 3050 Ti Laptop.

| Fase | Tiempo |
|---|---:|
| Lectura DXF (27 MB) | 26 s |
| Topología + alturas + extrusión | 40 s |
| Instancing | 5 s |
| Escritura GLB | 9 s |
| **Total** (`extrude convert`) | **83 s** |

Resultado: 1 675 010 triángulos, 3 draw calls (los árboles instanciados) y un GLB de 34 MB.
En el visor, a 2133×950, se mantiene en **60 FPS** (límite de vsync) en Chrome, **con sombras**
(mapa de 4096 ajustado a la zona visible; 3,35 M triángulos por fotograma contando la pasada de sombras).

## Estructura

```
pipeline/   Python: CLI `extrude`, conversión, lectura DXF, extrusión, export GLB
viewer/     TypeScript + Three.js: visor, iluminación, vistas y exportación
samples/    DXF sintéticos, sus configs (samples/config) y GLB de ejemplo (samples/glb)
tools/      Node: gltf-validator y gltf-transform (validación y compresión)
docs/       Arquitectura, configuración y formatos de exportación
```
