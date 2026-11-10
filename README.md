# Extrude

Convierte planos DWG/DXF urbanos (proyecto, manzana, barrio o ciudad) en modelos 3D glTF/GLB,
y los previsualiza en un visor web con control de sol, luces y vistas exportables a Blender.

> Estado: **Fase 2** (topología, extrusión y configuración por proyecto; aún sin export GLB). Ver [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Requisitos

| Herramienta | Versión | Instalación |
|---|---|---|
| Python | ≥ 3.12 (probado con 3.13) | <https://www.python.org> o `pyenv` |
| uv | ≥ 0.4 | `pip install uv` o <https://docs.astral.sh/uv/> |
| Node.js | ≥ 22.12 | <https://nodejs.org> |
| just | ≥ 1.38 | `uv tool install rust-just`, `winget install Casey.Just`, `brew install just` |

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

## Estructura

```
pipeline/   Python: CLI `extrude`, conversión, lectura DXF, extrusión, export GLB
viewer/     TypeScript + Three.js: visor, iluminación, vistas y exportación
samples/    DXF sintéticos y GLB de ejemplo
docs/       Arquitectura, configuración y formatos de exportación
```
