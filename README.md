<p align="center">
  <img src="viewer/public/icon-192.png" width="84" height="84" alt="Logo de Extrude" />
</p>

<h1 align="center">Extrude</h1>

<p align="center"><strong>De plano a modelo 3D.</strong><br />DWG / DXF · Terreno · Estudio solar · Vistas para Blender</p>

![Extrude: de plano a modelo 3D](docs/assets/cover.svg)

Convierte planos DWG/DXF urbanos (proyecto, manzana, barrio o ciudad) en modelos 3D glTF/GLB,
y los previsualiza en un visor web con control de sol, luces y vistas exportables a Blender.

**Python + TypeScript + Three.js.** Conversión por capas, informes trazables y visor local con
temas claro y oscuro. Versión de desarrollo: **0.1.0**.

[Inicio rápido](#inicio-rápido) · [Configuración](#configuración-por-proyecto) ·
[Visor](#visor) · [Exportación](#vistas-y-exportación) · [Desarrollo](#desarrollo-y-validación) ·
[Limitaciones](#limitaciones)

## Qué puedes hacer

- Convertir plantas, bloques y geometría 3D compatible en GLB, con unidades en metros y origen recentrado.
- Configurar capas, alturas, plantas y materiales mediante YAML; generar un borrador desde el dibujo.
- Reconstruir terreno desde curvas o puntos acotados y adaptar edificios mediante zócalos.
- Revisar alturas supuestas, descartes, flotación, enterramientos y huellas fuera del TIN, con identificadores del plano.
- Explorar el modelo por capas, medir distancias y estudiar el sol por fecha y ubicación.
- Guardar vistas con miniaturas y exportar PNG, views.json, scripts de Blender o un GLB con cámaras y luces.
- Dividir modelos grandes en teselas e instanciar bloques compatibles para reducir draw calls.

## Inicio rápido

Desde la carpeta del repositorio, con los [requisitos](#requisitos) instalados:

```sh
just setup
```

Abre dos terminales:

```sh
# Terminal 1: conversión de planos DWG/DXF
just serve
```

```sh
# Terminal 2: visor web
just dev
```

Abre **[localhost:5173](http://localhost:5173)**. Los tres modelos de ejemplo se preparan
automáticamente desde `samples/glb/`: puedes probar el visor sin un conversor DWG instalado.
El servidor de conversión escucha en **[127.0.0.1:8765](http://127.0.0.1:8765/api/health)**.
Para abrir únicamente GLB/GLTF o los ejemplos no hace falta arrancar `just serve`.

### Sin just

```sh
uv sync --project pipeline
npm ci --prefix viewer
npm ci --prefix tools
```

Después, en terminales separadas: `uv run --project pipeline extrude serve` y
`npm run dev --prefix viewer`.

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
  - Windows: descomprime el `.zip` de <https://github.com/LibreDWG/libredwg/releases> en
    `%LOCALAPPDATA%\Programs\libredwg-X.Y` (se detecta solo). Probado con 0.14 y un DWG 2018 real.
  - Ruta personalizada: `EXTRUDE_DWG2DXF_PATH`.
- **Manual**: en AutoCAD/BricsCAD, `SAVEAS` → DXF (versión 2018 o 2013).

## Comandos habituales

```sh
just setup     # instala dependencias de pipeline, visor y herramientas glTF
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

Genera `model.glb` (o `tiles/*.glb` + `manifest.json`), `report.md`, `report.json` y
`objects.json`. El informe lista todo lo leído, convertido, ignorado, reparado o descartado, con
su capa. `objects.json` es el catálogo de objetos: un ID persistente y una etiqueta (E-001…) por
edificio, sus superficies (fachada, patio, cubierta, zócalo) y la revisión geométrica. Un plan de
materiales se valida contra él con
`uv run extrude validate-materials materials.json --objects ../out/proyecto/objects.json` y se
aplica, sin volver a convertir, con
`uv run extrude materialize ../out/proyecto/model.glb materials.json`: el resultado
(`model.materialized.glb`) lleva materiales PBR y texturas incorporadas, y se abre en el visor o
en Blender. `uv run extrude library` lista los materiales disponibles.
Formatos: [docs/EXPORT_FORMATS.md](docs/EXPORT_FORMATS.md); requisitos de materialidad:
[docs/REQUISITOS_MATERIALIDAD_IA.md](docs/REQUISITOS_MATERIALIDAD_IA.md).

Con las muestras: `just convert-samples` y `just validate out/block_2d/model.glb`.

## Convertir arrastrando al visor

```sh
just serve   # servidor local de conversión (http://127.0.0.1:8765)
just dev     # visor
```

Arrastra un `.dwg` o `.dxf` al visor. Si antes sueltas (o eliges con `Config YAML…`) el YAML del
proyecto, se usa ese; si no, se genera un borrador automático. Tras convertir aparecen enlaces al
GLB, al informe y a la configuración usada o generada. El servidor solo escucha en local, limita el
tamaño (`--max-mb`) y conserva los 10 últimos trabajos. Mientras convierte, el visor muestra la
fase real (leyendo entidades, construyendo geometría 3D, exportando…): usa
`POST /api/convert?async=1` y consulta `GET /api/jobs/<id>/status`. Sin `async=1` la API sigue
respondiendo de forma síncrona. Atiende una conversión y hasta 4 en cola; con la cola llena responde 503 con
`Retry-After` sin leer el plano, y corta las conexiones que dejan de enviar datos durante 30 s. Solo convierte para los orígenes
permitidos (`--origin`; los navegadores deben enviar `application/octet-stream`) y, escuchando en
local, solo acepta `Host` de `localhost`/`127.0.0.1` (protección frente a *DNS rebinding*).

El visor muestra el destino efectivo de los planos antes de subirlos. `?server=` prevalece
sobre `VITE_EXTRUDE_SERVER`; si el enlace introduce un destino remoto distinto del configurado,
es necesario marcar la aceptación de envío antes de elegir o soltar el plano. La aceptación
solo dura mientras la página permanece abierta. Los servidores remotos configurados también
se identifican como remotos; los destinos inválidos bloquean la subida.

Al abrir el resultado aparece un resumen plegable de calidad: alturas por defecto, ambiguas,
descartes por falta de altura y comprobaciones de apoyo, con enlaces al informe completo y al
YAML usado. Las intersecciones con ladera se muestran aparte y no se tratan como flotación.
El informe detalla cada caso de apoyo con handles del DXF, coordenadas originales, cota de
origen, base y cubierta generadas, altura asignada y rango del terreno. Los enterrados
parcialmente requieren revisión. Por defecto los edificios con `base_from_terrain` mantienen
la planta en la cota máxima y añaden un zócalo hasta el terreno, con cubierta horizontal.
El informe identifica estos zócalos automáticos y su altura máxima. El apoyo antiguo en la
cota mínima se conserva con `terrain_support: minimum`. El informe también
indica si la huella sale del TIN y usa cotas estimadas por proximidad.

Durante la conversión puedes **Cancelar espera**. Esto detiene la espera del visor; el trabajo
puede continuar en el servidor. **Retomar espera** consulta el mismo trabajo sin reenviar el
archivo. Si la conversión terminó pero falló la descarga, **Reabrir resultado** reutiliza sus
archivos. Si el trabajo falló o caducó, **Reintentar envío** vuelve a subir el plano. La espera
tiene un límite total de 15 minutos y cada petición de conversión/estado, de 60 segundos.

Para planos muy grandes, abre el visor con `?tiles=500`: el servidor convierte en teselas de 500 m
(`tiles/` + `manifest.json`) y el visor las carga todas juntas, con las capas unificadas. Un
conjunto teselado también se abre con `?model=<url>/manifest.json`. El GLB con cámaras y luces
no está disponible para teselas (no hay un GLB único). Puedes exportar views.json; para generar
el script de Blender, convierte sin teselas. La exportación Blender de teselas aún no está disponible.

Si el YAML tiene `crs` UTM (p. ej. `"EPSG:25830"`), el sol se sitúa en el centro del plano.

## Visor

Los modelos abiertos por URL resuelven sus buffers y texturas respecto a la URL final del
modelo o de cada tesela, incluso tras redirecciones. Los archivos glTF elegidos desde el
equipo deben ser autocontenidos: si dependen de archivos auxiliares, usa un GLB autocontenido
o sirve el glTF y sus recursos mediante una URL. No se buscan esos archivos en la carpeta del visor.

Al importar vistas se validan dimensiones (enteros de 1 a 16384), cámara, clipping, vectores,
fechas, luces y el resto del estado antes de restaurarlo; los errores identifican el campo.

```sh
just dev                # http://localhost:5173
```

**Pantalla de inicio.** Sin modelo cargado solo se ve el hub:
- zona de arrastre para `.dwg`/`.dxf` (con `just serve`), `.glb`/`.gltf` y, opcionalmente, el `.yaml` del proyecto;
- botón *Examinar equipo*;
- tres ejemplos de un clic (manzana 2D, manzana UTM y bloques anidados), preparados al arrancar;
- barra de progreso real: descarga en %, carga de geometría y sombras.

Al terminar, el hub se desvanece y aparece el espacio de trabajo. *Abrir* (barra superior) lo vuelve a mostrar.

| Zona | Contenido |
|---|---|
| Barra superior | Modelo y su origen UTM/CRS, *Abrir*, perspectiva/ortográfica, *Encuadrar* (F), menú *Exportar* (PNG, Blender, views.json, GLB con cámaras y luces, importar vistas, resultados de la conversión), modo zen (Tab) |
| Panel izquierdo | **Vistas**: formato de render, marco de encuadre, tarjetas con miniatura (restaurar, renombrar, actualizar, duplicar, reordenar, borrar). **Capas**: buscador, visibilidad, color, aislar, triángulos |
| Panel derecho | **Sol** (línea de tiempo 0–24 h con amanecer y ocaso, ▶ reproducir, fecha, zona horaria, coordenadas, modo manual, norte real) · **Sombras** (No / Media 2K / Alta 4K, suavizado, avanzado) · **Entorno** (cielo, color o HDRI; suelo) · **Cámara y render** (FOV, tone mapping, exposición, vuelo) · **Luces** |
| Sobre la vista | Cubo de orientación (planta, alzados N/S/E/O, ISO) con aguja del norte real · píldora de FPS, triángulos y draw calls · tarjeta del objeto pulsado (altura, plantas, cota, capa, *Enfocar*) · regla **Medir** (M): dos clics dan distancia 3D, en planta, desnivel y pendiente · avisos |

Atajos: **F** encuadrar · **N** nueva vista · **M** medir · **Espacio** reproducir el día ·
**Tab** modo zen · **Esc** cerrar el hub o borrar la medición · doble clic: centrar en un punto. `?debug` expone la app en
`window.__extrude`.
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
## Limitaciones

- **DWG necesita un conversor externo.** Sin ODA o LibreDWG puedes trabajar con DXF y los modelos de ejemplo.
- **El YAML automático es un borrador.** Los valores por bloque, plantas y alturas por defecto deben contrastarse con el proyecto.
- **El zócalo es una aproximación geométrica.** Mantiene la planta horizontal y llega al terreno; no calcula una cimentación estructural ni modifica la topografía.
- **Fuera del TIN se extrapola por proximidad.** El informe avisa aunque el objeto parezca apoyado. La interpolación tampoco certifica la exactitud de las curvas originales.
- **Altura cero representa una superficie.** Con apoyo automático sigue el terreno y no genera un zócalo.
- **ACIS no se convierte directamente.** Los sólidos no soportados se enumeran en el informe; conviértelos a malla en el programa CAD.
- **GLTF local debe ser autocontenido.** Para buffers o texturas auxiliares abre el modelo por URL, o prepara un GLB autocontenido.
- **Teselas:** se pueden exportar vistas JSON; la exportación Blender y el GLB único con cámaras requieren una conversión sin teselas.
- **Uso local:** el servidor limita trabajos y tamaño de entrada, pero no incluye autenticación ni está diseñado como servicio público multiusuario.

## Desarrollo y validación

```sh
just check                       # lint, formato, tipado y pruebas de Python y TypeScript
just convert-samples             # conversiones de aceptación en out/
just validate out/block_2d/model.glb
npm run build --prefix viewer    # compilación de producción en viewer/dist/
```

También puedes ejecutar las comprobaciones por separado:

| Comando | Función |
|---|---|
| `just test-pipeline` / `just test-viewer` | Pruebas del conversor y del visor |
| `just lint-pipeline` / `just lint-viewer` | Ruff, mypy, TypeScript, ESLint y Prettier |
| `just fmt` | Formato de código |
| `just samples` | Regenerar DXF sintéticos |
| `just doctor` | Detectar conversores DWG |
| `just inspect "samples/mi plano.dxf"` | Inspeccionar un plano; admite espacios |
| `just bench` | Generar y convertir la ciudad sintética de 50 000 edificios |
| `just icons` | Regenerar logo, favicons y PNG |

Las pruebas que dependen de Blender o de herramientas externas pueden omitirse cuando no
están instaladas. Las herramientas glTF se instalan con `just setup-tools`.
La integración continua se define en [`.github/workflows/check.yml`](.github/workflows/check.yml).
Consulta [CONTRIBUTING.md](CONTRIBUTING.md) para el flujo de cambios.

### API local

| Método y ruta | Función |
|---|---|
| `GET /api/health` | Estado, conversores y capacidad de cola |
| `POST /api/convert?name=plano.dxf` | Conversión síncrona; cuerpo binario |
| `POST /api/convert?name=plano.dxf&async=1` | Aceptar un trabajo y devolver su ruta de estado |
| `GET /api/jobs/<id>/status` | Fase, progreso y resultado o error |
| `GET /api/jobs/<id>/…` | Modelo, informes, YAML, manifest y teselas |

El cliente envía `Content-Type: application/octet-stream` y, opcionalmente,
`X-Extrude-Config` con el YAML en Base64 UTF-8. `tiles` y `compress` son parámetros opcionales.
Cola llena: **503** con `Retry-After`, sin leer el cuerpo. YAML inválido: **422** con diagnóstico.

### Logo y favicon

La marca representa un volumen isométrico extruido. La geometría canónica está en
[`viewer/scripts/make-icons.py`](viewer/scripts/make-icons.py); `just icons` usa Pillow en
un entorno temporal de uv, sin añadirlo a las dependencias de ejecución.

| Recurso | Uso |
|---|---|
| `viewer/public/favicon.svg` | Icono vectorial del navegador |
| `viewer/public/favicon.ico` | Compatibilidad ICO: 16, 32 y 48 px |
| `viewer/public/apple-touch-icon.png` | Icono Apple de 180 px |
| `viewer/public/icon-192.png` y `icon-512.png` | Iconos del manifest y presentación del proyecto |
| `viewer/public/logo-mark.svg` | Símbolo sin fondo |
| `viewer/public/site.webmanifest` | Nombre, colores e iconos de la aplicación |
| `docs/assets/cover.svg` | Portada vectorial del README |

Los recursos de marca están versionados. Los decodificadores y las copias de modelos en
`viewer/public/models/` se preparan al ejecutar `dev` o `build`; no se versionan.
El manifest no implica soporte sin conexión: no se incluye un service worker.

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
.github/    Integración continua y plantillas de colaboración
```

## Documentación

- [Configuración por capa y fuentes de altura](docs/CONFIG.md)
- [Formatos de exportación y coordenadas](docs/EXPORT_FORMATS.md)
- [Arquitectura y decisiones técnicas](docs/ARCHITECTURE.md)
- [Cómo contribuir y preparar cambios](CONTRIBUTING.md)
- [Historial de cambios](CHANGELOG.md)

## Licencia

Todavía no se ha definido una licencia de distribución para este repositorio.
