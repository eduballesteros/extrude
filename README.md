# Extrude

Convierte planos DWG/DXF urbanos (proyecto, manzana, barrio o ciudad) en modelos 3D glTF/GLB,
y los previsualiza en un visor web con control de sol, luces y vistas exportables a Blender.

> Estado: **Fase 0** (esqueleto, tooling y `extrude doctor`). Ver [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

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

## Estructura

```
pipeline/   Python: CLI `extrude`, conversión, lectura DXF, extrusión, export GLB
viewer/     TypeScript + Three.js: visor, iluminación, vistas y exportación
samples/    DXF sintéticos y GLB de ejemplo
docs/       Arquitectura, configuración y formatos de exportación
```
