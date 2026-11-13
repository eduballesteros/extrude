# Guía de uso de Extrude

Extrude convierte planos urbanos DWG/DXF (un proyecto, una manzana, un barrio o una ciudad) en
modelos 3D GLB, y los abre en un visor web donde puedes estudiar el sol, guardar vistas, asignar
materiales, editar el proyecto con lenguaje natural y exportar a Blender.

Esta guía está ordenada por tareas. Si es tu primera vez, sigue los apartados 1 a 4; el resto
sirve de consulta.

**Elige por dónde empezar**

- **Quiero probarlo:** abre [el primer recorrido](#2-primer-recorrido-con-los-ejemplos) con Manzana 2D; no necesitas preparar un plano ni activar una API de IA.
- **Tengo un DWG o DXF:** empieza por [preparar tu plano](#3-preparar-tu-plano), con sus unidades, alturas y configuración.
- **Ya tengo un modelo:** consulta [materiales](#7-materiales), [edición con IA](#8-editar-el-proyecto-con-ia) y [vistas y exportación](#6-vistas-y-exportación).

La [guía del navegador](../viewer/guia.html) incluye búsqueda por temas (también sin tildes),
accesos por tarea y botones para copiar los bloques de texto. La búsqueda se ejecuta localmente;
no consulta a la IA. Para usar la guía interactiva, abre **Guía de uso** desde el inicio del visor.

```
Plano DWG/DXF ──► Configuración YAML ──► Conversión ──► model.glb + informe
                                                            │
                     Visor web ◄────────────────────────────┘
                     · capas, medir, sol, sombras, vistas
                     · materiales (manual o con IA)
                     · edición con IA: altura, mover, girar, duplicar
                     · exportar: PNG, GLB con cámaras, views.json, Blender
```

**Contenido**

1. [Instalar y arrancar](#1-instalar-y-arrancar)
2. [Primer recorrido con los ejemplos](#2-primer-recorrido-con-los-ejemplos)
3. [Preparar tu plano](#3-preparar-tu-plano)
4. [Convertir](#4-convertir)
5. [El visor](#5-el-visor)
6. [Vistas y exportación](#6-vistas-y-exportación)
7. [Materiales](#7-materiales)
8. [Editar el proyecto con IA](#8-editar-el-proyecto-con-ia)
9. [Revisiones, deshacer y archivos del proyecto](#9-revisiones-deshacer-y-archivos-del-proyecto)
10. [Referencia de comandos](#10-referencia-de-comandos)
11. [Solución de problemas](#11-solución-de-problemas)
12. [Limitaciones](#12-limitaciones)
13. [Glosario](#13-glosario)

---

## 1. Instalar y arrancar

### Requisitos

| Herramienta | Versión | Para qué |
|---|---|---|
| Python | 3.12 o superior | Conversor (`pipeline/`) |
| uv | 0.4 o superior | Entorno y dependencias de Python |
| Node.js | 22.12 o superior | Visor (`viewer/`) y herramientas glTF |
| just | 1.38 o superior | Atajos de comandos (opcional pero cómodo) |
| ODA File Converter o LibreDWG | — | Solo para abrir `.dwg`; sin ellos, usa `.dxf` |
| Blender | 4.2 o superior | Opcional: abrir el resultado con cámaras y luces |

Para leer DWG necesitas un conversor DWG → DXF. El recomendado es **ODA File Converter**
(gratuito con registro); también sirve **LibreDWG** (`dwg2dxf`). Si están en una ruta no
estándar, indícala con `EXTRUDE_ODA_PATH` o `EXTRUDE_DWG2DXF_PATH`. `just doctor` te dice cuál
detecta. Alternativa manual: en AutoCAD o BricsCAD, `GUARDARCOMO` → DXF 2018 o 2013.

### Instalación

Desde la carpeta del repositorio:

```sh
just setup
```

Sin `just`:

```sh
uv sync --project pipeline
npm ci --prefix viewer
npm ci --prefix tools
```

### Arranque

Abre dos terminales:

```sh
just serve   # servidor de conversión, materiales e IA → http://127.0.0.1:8765
just dev     # visor → http://localhost:5173
```

Abre **http://localhost:5173** en el navegador.

- Para **abrir GLB** o los ejemplos basta con `just dev`.
- Para **convertir planos**, usar **materiales** o **editar con IA** necesitas también `just serve`.

### Activar la edición con IA (opcional)

La IA la usa solo el servidor; las claves nunca llegan al navegador.

```sh
cd pipeline
uv sync --extra ai                     # instala los SDK de Anthropic y OpenAI
export ANTHROPIC_API_KEY=…             # para Claude
export OPENAI_API_KEY=…                # para OpenAI
export EXTRUDE_OPENAI_MODEL=…          # modelo de OpenAI contratado
uv run extrude serve
```

Para probar el flujo sin coste ni red: `uv run extrude serve --ai-simulated` añade el proveedor
**Simulado**, un intérprete de reglas local (no es IA). Al arrancar, el servidor indica qué
proveedores están listos y, si no, qué falta. En Windows (PowerShell) usa `$env:ANTHROPIC_API_KEY="…"`.

**Windows (PowerShell):** desde la carpeta del repositorio, ejecuta solo el bloque de tu proveedor,
sustituyendo los valores de ejemplo. Arranca el servidor en esa misma terminal.

Claude:

```powershell
cd pipeline
uv sync --extra ai
$env:ANTHROPIC_API_KEY="TU_CLAVE"
uv run extrude serve
```

OpenAI:

```powershell
cd pipeline
uv sync --extra ai
$env:OPENAI_API_KEY="TU_CLAVE"
$env:EXTRUDE_OPENAI_MODEL="TU_MODELO"
uv run extrude serve
```

---

## 2. Primer recorrido con los ejemplos

**Empieza sin configurar tu proyecto.** Con el visor arrancado, abre **Manzana 2D** en la pantalla
de inicio. No necesitas activar una API de IA. Si todavía no has arrancado el visor, sigue
[los pasos de instalación](#1-instalar-y-arrancar).

1. Con `just dev` en marcha, abre el visor. Verás la **pantalla de inicio** con tres ejemplos de
   un clic: manzana 2D, manzana en coordenadas UTM y bloques anidados.
2. Pulsa **Manzana 2D**. Cuando termine la carga aparece el espacio de trabajo.
3. Gira la vista arrastrando, acerca con la rueda, pulsa **F** para encuadrar todo.
4. Pulsa un edificio: la tarjeta muestra su etiqueta (E-001…), altura, plantas, cota y capa.
5. En el panel derecho, arrastra la línea de tiempo del **Sol** o pulsa **Espacio** para ver el
   recorrido del día y sus sombras.
6. Pulsa **N** para guardar la vista actual; aparece en la pestaña **Vistas**.
7. Abre **Exportar → PNG** para obtener una imagen.

Ya conoces lo básico. El resto de la guía explica cada parte en detalle.

---

## 3. Preparar tu plano

Cada estudio dibuja distinto: nombres de capa, cómo indica las alturas, unidades. Extrude no
presupone nada; todo se declara en un **YAML del proyecto**.

### 3.1 Inspeccionar el plano

```sh
cd pipeline
uv run extrude inspect ../planos/manzana.dxf
```

Muestra unidades, extensión, entidades por tipo y capa, ejemplos de textos (para detectar
rótulos de plantas como `4P` o `B+3`), atributos de bloque, entidades no soportadas y avisos.
Si `$INSUNITS` falta o es incorrecto, fuerza las unidades con `--units mm` (o `cm`, `m`…).

### 3.2 Generar un borrador de configuración

```sh
uv run extrude init-config ../planos/manzana.dxf -o ../proyecto.yaml
```

El borrador adivina el tipo de cada capa por su nombre (`EDIF*` → edificio, `VIAL*` → vial,
`AGUA` → agua, `VERDE` → vegetación, `CURVA`/`NIVEL` → terreno) y de dónde sale la altura de los
edificios (atributos de bloque, textos dentro de la planta, tipo de bloque, cota Z o, si no hay
nada, 9 m por defecto). Cada regla lleva comentarios con la evidencia encontrada.

**Revísalo siempre**: es un punto de partida, no una configuración verificada.

### 3.3 Ajustar el YAML

Estructura mínima:

```yaml
units: auto            # auto | mm | cm | m | km | in | ft
recenter: true         # imprescindible con coordenadas UTM
crs: "EPSG:25830"      # opcional: sitúa el sol en la ubicación real
layers:                # gana la PRIMERA regla cuyo patrón encaje
  - match: "EDIFIC*"
    kind: building
    height_from: text          # altura leída de rótulos dentro de cada planta
    text_regex: '(\d+)\s*P'    # «4P» → 4 plantas
    floor_height: 3.0          # metros por planta (también lo usa la IA para «sube dos plantas»)
  - match: "VIALES"
    kind: road
  - match: "CURVAS"
    kind: terrain              # curvas de nivel → terreno
  - match: "*"
    kind: ignore               # todo lo demás no se convierte
```

Tipos de capa (`kind`):

| Tipo | Resultado |
|---|---|
| `building` | Volumen extruido (con patios interiores) según su altura |
| `road`, `water`, `vegetation` | Superficie plana (o adaptada al terreno) |
| `terrain` | Terreno triangulado desde curvas de nivel y puntos acotados |
| `line` | Líneas 3D sin extruir |
| `ignore` | Nada (se cuenta en el informe) |

Altura de cada edificio, por orden de prioridad: `height_from` (texto, atributo, bloque o cota)
→ `floors` × `floor_height` → `height` → `default_height`. Un edificio sin ninguna se **descarta**
y aparece en el informe como «sin altura».

Opciones útiles:

- **Rótulos tipo Catastro** (`IV`, `II`): `text_regex: '^\s*([IVXL]+)\b'`.
- **«B+3»** = baja + 3: `text_regex: 'B\s*\+\s*(\d+)'` y `floor_offset: 1`.
- **Atributo de bloque**: `height_from: attribute`, `attribute: ALTURA`, `value_means: meters`.
- **Apoyar en el terreno**: lo hace solo `base_auto: true`, que es lo que escribe el
  borrador. Cada objeto se apoya sobre el terreno bajo su huella; los edificios mantienen la
  planta horizontal y añaden un zócalo. Ver [APOYO_Y_COTAS.md](APOYO_Y_COTAS.md).
- **Bloques repetidos** (árboles, mobiliario): se instancian solos si hay al menos
  `instancing_min` copias iguales (10 por defecto), lo que acelera el visor.

Referencia completa con todas las claves y ejemplos: [CONFIG.md](CONFIG.md).

---

## 4. Convertir

### 4.1 Desde el visor (lo más cómodo)

Con `just serve` en marcha:

1. En la pantalla de inicio, suelta primero el `.yaml` del proyecto (o elígelo con
   **Config YAML…**). Si no lo haces, se genera un borrador automático.
2. Suelta el `.dwg` o `.dxf` (o usa **Examinar equipo**).
3. El visor muestra la fase real: leyendo entidades, construyendo geometría, exportando…
4. Al terminar se abre el modelo y aparecen enlaces al GLB, al informe y a la configuración
   usada o generada (también en **Exportar → resultados de la conversión**).

Durante la espera:

- **Cancelar espera** deja de esperar, pero la conversión sigue en el servidor.
- **Retomar espera** vuelve a consultar el mismo trabajo sin reenviar el plano.
- **Reabrir resultado** reutiliza los archivos si terminó pero falló la descarga.
- **Reintentar envío** vuelve a subir el plano si el trabajo falló o caducó.

El visor indica a qué servidor enviará el plano antes de subirlo. Si un enlace apunta a un
servidor remoto distinto del configurado, tendrás que aceptar el envío expresamente.

### 4.2 Desde la línea de comandos

```sh
cd pipeline
uv run extrude convert ../planos/manzana.dwg -c ../proyecto.yaml -o ../out/manzana
```

Genera en la carpeta de salida:

| Archivo | Contenido |
|---|---|
| `model.glb` | El modelo 3D |
| `report.md` / `report.json` | Todo lo leído, convertido, reparado o descartado, por capa |
| `objects.json` | Catálogo de objetos: ID persistente, etiqueta (E-001…), superficies y revisión |

### 4.3 Revisar la calidad

Al abrir un resultado aparece un **resumen de calidad** plegable: alturas por defecto o ambiguas,
edificios descartados por falta de altura y comprobaciones de apoyo (flotantes, enterrados,
huellas fuera del terreno). El **informe** completo identifica cada caso con los handles del
DXF y sus coordenadas originales, para localizarlos en el CAD.

### 4.4 Planos muy grandes

- **Teselas**: abre el visor con `?tiles=500` (o usa `--tiles 500` en la CLI) para dividir el
  modelo en piezas de 500 m que se cargan juntas.
- **Compresión**: `--compress meshopt` (o `draco`) reduce el tamaño del GLB.

Con teselas o compresión, la conversión guarda además un **maestro editable**
(`model.master.glb`, sin comprimir y de una pieza). Los materiales, la edición con IA y deshacer
o rehacer trabajan sobre él:

- **Proyecto teselado:** cada revisión confirmada, y cada deshacer o rehacer, regenera
  `tiles/*.glb` y `manifest.json` desde el estado vigente, con la misma regla de reparto que la
  conversión (centro del objeto y tamaño de tesela). Si un objeto se mueve a otra tesela, cambia
  de archivo. Las teselas vacías desaparecen del manifest; cada revisión usa nombres propios
  y conserva los archivos anteriores para las cargas ya iniciadas. En el visor editas sobre
  el maestro completo.
- **GLB comprimido** (convertido con `--compress` o abierto desde disco): se decodifica
  (Draco, Meshopt, cuantización) a un maestro editable que conserva los IDs, las etiquetas y las
  instancias. El original no se modifica. En **Exportar** aparecen *GLB actual comprimido
  (Meshopt)* y *(Draco)*, que comprimen el estado vigente.

Las teselas originales numeran las copias de bloque por tesela. Tras la primera edición, cada
copia conserva en todas las teselas el número que tiene en el maestro.

---

## 5. El visor

### 5.1 Pantalla de inicio

Zona de arrastre para `.dwg`/`.dxf` (con `just serve`), `.glb`/`.gltf` y el `.yaml` del proyecto;
botón **Examinar equipo**; los tres ejemplos; y una barra de progreso real (descarga, geometría,
sombras). **Abrir**, en la barra superior, vuelve a esta pantalla.

También puedes abrir un modelo por URL: `http://localhost:5173/?model=/ruta/model.glb` (o un
`manifest.json` de teselas).

### 5.2 Zonas de la pantalla

| Zona | Qué hay |
|---|---|
| **Barra superior** | Nombre del modelo y su origen UTM/CRS · **Abrir** · Perspectiva / Ortográfica · **Ajustar** (encuadrar) · **Medir** · menú **Exportar** · tema claro/oscuro · ocultar paneles · **Cerrar modelo** |
| **Panel izquierdo** | Pestañas **Vistas**, **Capas** y **Materiales** |
| **Panel derecho (Inspector)** | **Luz solar**, **Sombras**, **Entorno**, **Cámara**, **Postprocesado**, **Luces** |
| **Sobre la vista** | Cubo de orientación (planta, alzados, ISO) con aguja del norte · FPS, triángulos y draw calls · tarjeta del objeto pulsado · avisos |

### 5.3 Navegar

- **Arrastrar**: orbitar. **Rueda**: acercar/alejar. **Doble clic**: centrar en ese punto.
- **Cubo de orientación**: vistas de planta, alzados norte/sur/este/oeste e isométrica.
- **Perspectiva / Ortográfica** en la barra superior.
- **F**: encuadrar todo el modelo. En la tarjeta de un objeto, **Enfocar** lo encuadra a él.

### 5.4 Capas

La pestaña **Capas** lista las capas del modelo con un buscador. Para cada capa: visibilidad,
color, **aislar** (ver solo esa) y número de triángulos. Los colores de capa son solo de
visualización; al aplicar materiales se retiran para no teñir las texturas.

### 5.5 Inspeccionar un objeto

Pulsa cualquier objeto: la tarjeta muestra su etiqueta, ID, superficie pulsada (fachada, patio,
cubierta, zócalo…), altura, plantas, cota de base y capa. En las copias de bloque, el bloque y el
número de copia.

### 5.6 Medir

Pulsa **M** (o **Medir**) y haz dos clics: verás la distancia 3D, la distancia en planta, el
desnivel y la pendiente. **Esc** borra la medición. La regla no sale en los renders.

### 5.7 Sol, sombras y ambiente

- **Luz solar**: línea de tiempo de 0 a 24 h con amanecer y ocaso marcados, **▶** (o
  **Espacio**) para reproducir el día a 0,5×–4×, fecha, zona horaria, latitud y longitud, modo
  manual (azimut y elevación) y **Norte real** (giro del norte geográfico respecto al +Y del
  dibujo). Si el YAML declara un `crs` UTM, el sol se sitúa en la ubicación del plano.
- **Sombras**: sin sombras, definidas o suaves; suavizado, cobertura (automática, zona visible o
  toda la escena) y ajustes avanzados.
- **Entorno**: cielo procedural, color liso o imagen HDRI (**Cargar…**); luz ambiente; suelo con sombras.
- **Cámara**: campo de visión vertical y modo vuelo.
- **Postprocesado**: tone mapping (AgX, ACES, Neutral, lineal) y exposición.
- **Luces**: añade luces puntuales, focos o luces de área y colócalas con un clic sobre el modelo.

### 5.8 Atajos de teclado

| Tecla | Acción |
|---|---|
| **F** | Encuadrar |
| **N** | Nueva vista |
| **M** | Medir |
| **Espacio** | Reproducir / pausar el día |
| **Tab** | Modo zen (ocultar paneles) |
| **Esc** | Cerrar el inicio o borrar la medición |
| **Ctrl+Z / Ctrl+Y** | Deshacer / rehacer en el borrador de materiales |
| Doble clic | Centrar en un punto |

---

## 6. Vistas y exportación

### 6.1 Vistas

Una vista guarda cámara, sol, luces, capas y entorno, con una miniatura. En la pestaña **Vistas**:

- **Nueva vista** (o **N**) captura el estado actual;
- elige el **formato** de render (16:9, 4:3, 1:1… con su resolución) y activa el **marco** para ver el
  encuadre exacto que saldrá en el render;
- en cada tarjeta: restaurar (con transición de cámara), renombrar, actualizar con el estado
  actual, duplicar, reordenar y borrar.

Las vistas se guardan en el navegador, por modelo. Pertenecen al proyecto: aplicar materiales o
editar la geometría no las cambia.

### 6.2 Exportar (menú Exportar)

| Opción | Resultado | Úsalo para |
|---|---|---|
| **PNG** | Render de la vista actual a la resolución elegida | Imágenes rápidas |
| **Script Blender** | `blender_setup.py`: crea en Blender cámaras, sol, luces y visibilidad de capas por vista; `render_all()` las renderiza todas | Render final en Blender |
| **views.json** | Todas las vistas, con la cámara en coordenadas reales del DWG | Copia de seguridad, compartir vistas o importarlas en otro equipo |
| **GLB + cámaras y luces** | El modelo con una cámara por vista y las luces de la vista activa | Abrir en otros programas 3D |
| **Importar views.json…** | Recupera vistas exportadas | — |

Para Blender: copia `blender_setup.py` junto al GLB (si confirmaste materiales, el
`<modelo>.materialized.glb` que descargas desde la pestaña Materiales) y ejecútalo en Blender
(**Scripting → Abrir → Ejecutar**) o con `blender -b -P blender_setup.py`.

Mientras haya una **previsualización de materiales o de cambios sin confirmar**, las
exportaciones se bloquean: confirma o descarta antes, para no exportar algo provisional.

---

## 7. Materiales

Pestaña **Materiales** (necesita `just serve`). Asigna materiales reales con textura a cada
edificio y superficie, sin cambiar la geometría.

### 7.1 Conceptos

- **Objeto**: cada edificio, vial o copia de bloque, con una etiqueta estable (E-001, V-001,
  KIOSCO #3…). Los edificios se numeran de norte a sur y de oeste a este.
- **Superficie**: fachada exterior, fachada de patio, cubierta, zócalo, suelo… Cada objeto
  ofrece solo las que tiene.
- **Biblioteca**: 12 materiales (ladrillo claro y rojo, hormigón visto, revestimiento blanco,
  piedra gris, asfalto, grava de cubierta, madera, metal oscuro, vidrio, vegetación, agua).
- **Pieza**: tamaño real de un ladrillo, losa o tabla (p. ej. ladrillo de 0,25 × 0,067 m). Para
  materiales sin piezas (asfalto, revoco) el campo es la **repetición** de la textura.

### 7.2 Asignar a mano

1. Marca objetos en la lista (filtrable por etiqueta o capa), pulsa uno en la vista y usa
   **+ Pulsado en 3D**, o **Marcar filtrados**.
2. Elige **Superficie** (indica cuántos de los marcados la tienen) y **Material**.
3. Ajusta, si quieres, **Color**, **Rugosidad**, **Pieza (m)** y **Giro (°)**.
4. **Añadir asignación**. Si ya había otra para ese objeto y superficie, la nueva la sustituye.
5. **Deshacer / Rehacer** (Ctrl+Z / Ctrl+Y) en la lista de asignaciones.

### 7.3 Previsualizar y confirmar

- **Previsualizar**: el servidor aplica los materiales y el modelo se recarga sin mover cámara,
  sol ni capas. Nada queda guardado todavía.
- **Descartar**: vuelve al último estado confirmado.
- **Confirmar**: guarda una revisión nueva (`mat-001`, `mat-002`…) y ofrece descargar el
  **GLB materializado** (texturas incluidas, se abre en Blender o cualquier visor glTF) y
  **materials.json** (el registro de asignaciones).

Funciona con modelos convertidos desde el visor y con GLB base abiertos desde disco (se suben al
servidor al previsualizar). Un GLB ya materializado no se vuelve a editar: abre su `model.glb`.

### 7.4 Importar y exportar el plan

**Exportar plan** guarda `materials.json`; **Importar plan** lo carga como borrador (avisa si es
de otra revisión geométrica o cita objetos que no existen). El borrador se recuerda en el
navegador. Si el plan guarda la descripción con que se pidió por IA, **Quitar descripción** la
elimina al confirmar, conservando las asignaciones.

### 7.5 Desde la línea de comandos

```sh
cd pipeline
uv run extrude library                                         # materiales disponibles
uv run extrude validate-materials materials.json --objects ../out/manzana/objects.json
uv run extrude materialize ../out/manzana/model.glb materials.json   # → model.materialized.glb
```

---

## 8. Editar el proyecto con IA

Sección **Editar proyecto con IA**, arriba en la pestaña Materiales. Escribe libremente qué
quieres cambiar; una **única consulta** a la IA devuelve una interpretación y un plan de
operaciones que Extrude ejecuta de verdad sobre el modelo. Nada se aplica sin tu revisión.

### 8.1 Operaciones básicas de referencia

| Pide algo como… | Operación | Condiciones |
|---|---|---|
| «Ladrillo claro en las fachadas de E-001 y E-002» | Asignar material | Objetos con esa superficie |
| «Sube E-006 dos plantas», «altura de 20 m», «baja 3 m» | Cambiar altura | Edificios de cubierta plana que no sean copias de bloque. Las plantas usan la altura de planta configurada |
| «Mueve E-007 10 m al sur» | Mover | Edificios y copias de bloque; no se reapoya sobre terreno irregular |
| «Gira el KIOSCO #4 45 grados» | Girar | Alrededor de su centro, en planta |
| «Duplica E-002 40 m al este» | Duplicar | La copia recibe etiqueta nueva y hereda los materiales |
| «Prepara una vista al atardecer» | Crear vista | Encuadre y hora solar (amanecer, mediodía, atardecer u HH:MM) |
| «Pon el sol a las 18:30» | Cambiar sol | — |

### 8.1 bis Arquitectura y entorno

| Pide algo como… | Operación | Condiciones |
|---|---|---|
| «Coloca 10 árboles a lo largo de V-001 cada 5 m» | Colocar en el entorno | Recursos genéricos: árbol de copa, árbol columnar, arbusto, banco, farola y papelera. Se colocan en fila (junto a un camino o vial), alrededor de un objeto (los bancos miran al centro) o en un punto. Son copias instanciadas y se pueden mover, girar, duplicar y eliminar |
| «Pon 4 bancos alrededor de E-006» | Colocar en el entorno | Si no hay terreno, Extrude **pide la cota de apoyo** (nunca la inventa) |
| «Añade ventanas en la fachada sur de E-001» | Ventanas | Elementos añadidos sobre el muro (vidrio y carpintería), **sin perforarlo**. Por fachada orientada y por plantas, con la altura de planta conocida o pedida. No duplica ventanas que ya existen |
| «Balcones en las plantas 2 y 3 de E-001» | Balcones | Losa con barandilla, nunca en la planta baja. Siguen al edificio al moverlo o girarlo, y bajar el edificio por debajo de ellos se rechaza |
| «Haz un ático en E-006 retranqueado 2 m al norte» | Retranqueo | Por orientaciones o en todo el perímetro. Funciona con patios y huellas cóncavas. Se rechaza si el retranqueo hace desaparecer la planta. El ático hereda los materiales |
| «Independiza DOBLE #2.1 y súbela 3 m» | Independizar copia | Solo **esa** copia pasa a geometría propia, con los mismos IDs y su posición. Las demás copias no cambian. Para cambiar todas las copias a la vez hace falta otra capacidad (*block.edit*) |
| «Sube los muros de E-001 2 m» | Subir muros | Para cubiertas inclinadas: conserva la forma de la cubierta |
| «Cumbrera a 1,5 m» | Cumbrera | Altura de cumbrera sobre el alero en cubiertas inclinadas; en una cubierta plana es incompatible |

Puedes combinar varias en una petición y seguir sobre lo hecho: «mantén todo como está, pero
cambia la cubierta a grava».

**Comprueba el plan de tu versión.** Las operaciones disponibles y sus condiciones de
compatibilidad aparecen en la propuesta. Siguen sin estar disponibles: perforar huecos reales en
los muros, generar caminos, editar el terreno, pedir especies o productos concretos, cambiar la
pendiente de una cubierta por ángulo y editar un bloque en todas sus copias. Si una
petición no puede ejecutarse, debe aparecer como pendiente o no realizable, sin darse por hecha.

Peticiones estéticas («dale un aspecto mediterráneo») se traducen en operaciones concretas
marcadas como **supuesto**, sin marcar: decides tú si las aplicas.

### 8.2 Paso a paso

1. Elige **Proveedor** (Claude, OpenAI o Simulado) y **Alcance**: todo el modelo, solo los
   objetos marcados o una capa. Acotar reduce el coste y evita confusiones.
2. Escribe la petición en **Qué quieres cambiar** y pulsa **Proponer cambios**.
3. **La primera vez con cada proveedor** se muestra a quién se envía, qué datos salen y el
   contenido exacto. **Autorizar y enviar** envía exactamente lo que has revisado; si cambias el
   texto, el alcance o el proveedor con el diálogo abierto, hay que revisarlo de nuevo. Puedes
   consultarlo en cualquier momento con **Ver datos que se envían** y retirar el permiso con
   **Revocar autorización**.
4. Revisa la propuesta:
   - **Así he entendido tu petición**: la reformulación;
   - **Requisitos explícitos** y **Supuestos** (decisiones que debes revisar);
   - **Operaciones**, cada una con su estado, los objetos afectados (resaltados en naranja al
     pasar el ratón o con el ojo) y el impacto previsto, p. ej. «E-001 12 → 18 m»;
   - **Pendiente de aclarar**, **Conflictos** y **Aún no se puede hacer**.
5. Marca las operaciones que quieras. Al marcar una se marcan las que necesita antes; al
   desmarcarla, las que dependen de ella.
6. **Previsualizar marcadas**: el modelo cambia (y la cámara y el sol, si hay vistas) sin
   guardar nada. **Descartar previsualización** lo deshace.
7. **Confirmar**: los cambios quedan guardados como una revisión nueva del proyecto, y las
   vistas pedidas aparecen en la pestaña Vistas.

### 8.3 Estados de una operación

| Estado | Significa | Qué hacer |
|---|---|---|
| **Lista** | Se puede aplicar | Marcarla |
| **Elige objetos** | La referencia es ambigua («los del norte», «la central») | En «Pendiente de aclarar», **Usar candidatos** o marca otros objetos y **Usar marcados** |
| **Falta un dato** | Pediste plantas y ese edificio no tiene altura de planta configurada | Escribe la **Altura de planta (m)** en la operación |
| **Resuelta** | Ya diste lo que faltaba | Marcarla |
| **Bloqueada** | Hay un conflicto o depende de algo que no se puede aplicar | Reformula la petición |
| **No compatible** | Ningún objeto admite esa operación (p. ej. subir una copia de bloque) | Elige otros objetos |

Resolver dudas, dar datos, previsualizar y confirmar **no vuelven a consultar a la IA**.

Cómo se resuelven las referencias espaciales (reglas explícitas, siempre con tu confirmación):

- «Los del norte / sur / este / oeste»: edificios cuyo centro queda a ese lado del centro del
  conjunto. **Norte es el +Y del plano**, no el norte geográfico.
- «El más alto», «el más grande», «el central»: ordenando por altura, área en planta o cercanía al
  centro del conjunto.

### 8.4 Ejemplos

```
Sube E-001 dos plantas y pon grava en todas las cubiertas salvo E-004
Los edificios del norte de ladrillo claro
Mueve E-007 10 m al sur y gira el KIOSCO #4 45 grados
Duplica E-002 40 m al este
Coloca 10 árboles a lo largo de V-001 cada 5 m a ambos lados
Añade ventanas en la fachada sur de E-001 y balcones en las plantas 2 y 3 de E-001
Haz un ático en E-006 retranqueado 2 m al norte
Independiza DOBLE #2.1 y súbela 3 m
Mantén todo como está, pero cambia la cubierta de E-006 a grava
Prepara una vista desde el sur al atardecer centrada en E-006
Dale al conjunto un aspecto mediterráneo
```

Consejos: cita los objetos por su etiqueta (E-004) cuando puedas; marca en la lista los objetos
de los que hablas y usa el alcance **Solo marcados**; pide por partes los cambios grandes.

### 8.5 Coste y privacidad

- **Una pulsación, como mucho una consulta.** No hay consultas al escribir, seleccionar o mover
  la cámara, ni reintentos automáticos.
- Una petición **idéntica** sobre el mismo estado se sirve de la **caché** sin consultar.
  **Borrar caché de propuestas** la vacía.
- Tras **5 consultas** en un proyecto, cada consulta más pide confirmación.
- Debajo del botón ves cuántas consultas llevas y el consumo que informa el proveedor (tokens y
  duración). Solo se muestra un importe si el administrador configuró tarifas.
- **Cancelar** deja de esperar, pero el proveedor puede terminar y facturar la consulta; si
  termina, queda en caché y repetir la misma petición no vuelve a consultar.
- **Se envía**: tu petición, la revisión del proyecto, y por cada objeto del alcance su etiqueta,
  capa, tipo, bloque, tamaño, altura, superficies, materiales actuales y posición **relativa** en
  metros; además, la lista de herramientas y materiales.
- **No se envía**: el DWG/DXF, el GLB, imágenes, coordenadas absolutas, rutas ni claves.
- El texto que escribes se envía tal cual: no incluyas datos personales.

Configuración del servidor, límites y detalles técnicos: [EDICION_IA.md](EDICION_IA.md).

---

## 9. Revisiones, deshacer y archivos del proyecto

Cada vez que confirmas algo (materiales a mano, cambios con IA o solo vistas) el proyecto avanza
de revisión: `rev-000` es el modelo convertido, luego `rev-001`, `rev-002`… La revisión actual se
ve arriba en **Editar proyecto con IA**, con **Deshacer** y **Rehacer**:

- deshacer vuelve exactamente al estado anterior (geometría, materiales y vistas creadas);
- rehacer recupera lo deshecho; hacer un cambio nuevo después de deshacer descarta el rehacer;
- ninguna revisión se borra, así que siempre hay un resultado anterior recuperable;
- una propuesta hecha sobre otra revisión se considera **obsoleta** y hay que pedirla de nuevo.

En modo local, el servidor conserva los 10 últimos trabajos temporales. En modo compartido,
los proyectos con propietario quedan fuera de esa limpieza: al alcanzar la cuota por usuario
se rechazan nuevas cargas, sin borrar proyectos existentes. Los trabajos se guardan en la
carpeta temporal del sistema, o en la indicada con `extrude serve --jobs-dir`.
Dentro de cada trabajo, en `out/`:

| Archivo | Contenido |
|---|---|
| `model.glb` | Modelo convertido (no cambia nunca) |
| `model.current.glb` | Geometría vigente (con las ediciones) |
| `model.materialized.glb` | Geometría vigente con materiales: lo que se ve y se descarga |
| `materials.json` | Plan de materiales vigente |
| `project.json` | Historial de revisiones |
| `revisions/`, `materials/` | Cada revisión geométrica y cada plan confirmado |

Para conservar el trabajo a largo plazo, descarga el GLB materializado y `materials.json` desde
la pestaña Materiales y exporta tus vistas (`views.json`).

---

## 10. Referencia de comandos

| Comando | Para qué |
|---|---|
| `just setup` | Instalar todo |
| `just serve` / `uv run extrude serve` | Servidor local (conversión, materiales, IA) |
| `just dev` | Visor en http://localhost:5173 |
| `just doctor` | Ver qué conversor DWG está disponible |
| `extrude inspect plano.dxf [--json] [--units mm]` | Analizar un plano |
| `extrude init-config plano.dxf -o proyecto.yaml` | Borrador de configuración |
| `extrude convert plano.dwg -c proyecto.yaml -o salida/ [--tiles 500] [--compress meshopt]` | Convertir |
| `extrude library` | Biblioteca de materiales |
| `extrude validate-materials materials.json --objects objects.json` | Validar un plan |
| `extrude materialize model.glb materials.json [-o salida.glb] [--compress]` | Aplicar materiales |
| `just validate out/…/model.glb` | Validar un GLB con el validador de Khronos |
| `just check` | Pruebas y comprobaciones (desarrollo) |

Opciones de `extrude serve`: `--port`, `--host`, `--allowed-host` (nombres aceptados en la cabecera
Host al escuchar en todas las interfaces), `--trust-forwarded` (detrás de un proxy propio),
`--max-mb` (tamaño máximo del plano), `--origin`
(orígenes del visor permitidos, repetible), `--jobs-dir`, `--ai-simulated`, `--shared --users
ARCHIVO` (modo multiusuario).

| Comando | Para qué |
|---|---|
| `extrude users add ana --users usuarios.json` | Crear o cambiar un usuario (pide la contraseña; también `--password-stdin`) |
| `extrude users list --users usuarios.json` | Listar usuarios |

### Modo multiusuario

`extrude serve --shared --users usuarios.json` exige iniciar sesión: el visor muestra un diálogo
de acceso. Cada proyecto es de quien lo crea, y su dueño lo comparte con
`POST /api/jobs/<id>/access {"user": "bea", "role": "editor" | "viewer" | null}`. Quien no tiene
permiso recibe «no existe». Un lector ve el proyecto, pero no lo cambia. Toda confirmación
lleva la revisión base. Si otra persona confirmó antes, la tuya se rechaza (409) y no se
pierde nada. Por usuario hay límites de intentos de acceso, de trabajos y de consultas de IA por
hora.

El modo compartido **no abre el servidor a la red**: sigue escuchando en 127.0.0.1. Para usarlo
en una red local pasa `--host` de forma explícita, detrás de HTTPS (por ejemplo, con un proxy
inverso).

---

## 11. Solución de problemas

| Síntoma | Causa probable | Solución |
|---|---|---|
| «No se puede convertir DWG» | No hay conversor DWG → DXF | Instala ODA File Converter o LibreDWG (`just doctor`), o guarda como DXF |
| El modelo sale diminuto o gigante | Unidades mal declaradas | `units: mm` (o la que toque) en el YAML; compruébalo con `inspect` |
| Faltan edificios | No tenían altura | Revisa «sin altura» en el informe; añade `default_height` o ajusta `text_regex` |
| Alturas raras | Rótulos ambiguos o fuera de la planta | Revisa el resumen de calidad; acota `text_layers` y `text_tolerance` |
| Edificios flotando o enterrados | Apoyo en terreno (proyectos antiguos o base explícita) | En el visor, **Capas → Revisión del proyecto → Aplicar y reconvertir**; o `base_auto: true` en su regla |
| Todo lejísimos del origen | Coordenadas UTM sin recentrar | `recenter: true` (por defecto) |
| Materiales: «Sin biblioteca» | `extrude serve` no está en marcha | Arráncalo y recarga el visor |
| Materiales: «Este GLB ya está materializado» | Abriste un GLB con materiales | Abre su `model.glb` base |
| Materiales: «no tiene superficies por objeto» | GLB convertido con una versión antigua | Vuelve a convertir el plano |
| IA: proveedor «no disponible» | Falta la clave o el modelo | Mira el motivo en la sección; configura el servidor y reinícialo |
| IA: «Plan obsoleto» | El proyecto cambió desde la propuesta | Vuelve a pedirla |
| IA: «La solicitud cambió: revísala de nuevo» | Editaste algo con el diálogo de autorización abierto | Vuelve a pulsar **Proponer cambios** |
| IA: «Límite de consultas de la sesión» | Llevas 5 consultas en el proyecto | **Continuar con una consulta más** si quieres seguir |
| IA: «El alcance tiene N objetos…» | Demasiados objetos para una consulta | Marca los afectados o elige una capa |
| IA: «Confirma o descarta antes tus asignaciones manuales» | Hay un borrador manual sin confirmar | Confirma o descarta en el pie de la pestaña |
| Exportar dice que hay una previsualización | Hay cambios sin confirmar | Confirma o descarta |
| «Inicia sesión para usar este servidor compartido» | Servidor en `--shared` | Entra con tu usuario; pide una cuenta a quien administra el servidor |
| «Proyecto teselado sin GLB maestro» | Convertido con una versión anterior | Vuelve a convertir con `--tiles` |
| IA: «falta un dato: Cota de apoyo» | El modelo no tiene terreno | Escribe la cota en la operación |
| «No se puede escuchar en 127.0.0.1:8765» | Otro `extrude serve` sigue abierto (también en Windows: el puerto es exclusivo) | Ciérralo o usa `--port` (y abre el visor con `?server=http://127.0.0.1:PUERTO`) |

---

## 12. Limitaciones

### Formatos y entorno de trabajo

- DWG necesita un conversor externo; los sólidos ACIS no se convierten (expórtalos como malla).
- El GLB con cámaras y el script de Blender parten del estado actual de una sola pieza (el
  maestro en proyectos teselados); no se generan por tesela.
- El modo compartido (`--shared`) autentica y separa usuarios, pero el servidor no sirve HTTPS.
  Para exponerlo en una red, ponlo detrás de un proxy con TLS. Las sesiones viven en memoria:
  reiniciar el servidor obliga a entrar de nuevo.

### Datos y aproximaciones que debes revisar

- **Alturas:** el YAML automático es un borrador. Contrasta los valores por defecto con tu plano o introduce las alturas conocidas.
- **Terreno:** fuera del área triangulada las cotas se extrapolan. Comprueba el informe y aporta más datos si necesitas un apoyo preciso.
- **Zócalo:** es una aproximación geométrica del encuentro con el terreno; no equivale a una cimentación calculada ni modifica la topografía.

### Compatibilidad de la edición

Antes de confirmar, revisa las operaciones **Listas**, las **No compatibles** y las aclaraciones
del plan. Una petición escrita no implica que el cambio se haya ejecutado. Consulta las condiciones
de cada operación en [Editar con IA](#8-editar-el-proyecto-con-ia) y
[Solución de problemas](#11-solución-de-problemas).

---

## 13. Glosario

| Término | Significado |
|---|---|
| **GLB / glTF** | Formato estándar de modelos 3D; el GLB es un único archivo binario |
| **Etiqueta** | Nombre visible y estable de un objeto: E-001 (edificio), V-001 (vial), KIOSCO #3 (copia de bloque) |
| **ID persistente** | Identificador interno (`obj-…`) que no cambia mientras no cambie el dibujo |
| **Revisión geométrica** | Huella de la geometría; cambia al convertir otro plano o editar alturas, posiciones o copias |
| **Revisión de materiales** | `mat-001`, `mat-002`…: cada plan confirmado |
| **Revisión del proyecto** | `rev-000`, `rev-001`…: cada estado confirmado (geometría + materiales) |
| **Superficie** | Parte de un objeto: fachada exterior, fachada de patio, cubierta, zócalo, suelo |
| **Pieza / repetición** | Tamaño real de un ladrillo o losa / tamaño del mosaico de textura |
| **Zócalo** | Pared que une la planta horizontal de un edificio con el terreno inclinado |
| **TIN** | Terreno triangulado a partir de curvas de nivel y puntos acotados |
| **Copia de bloque (instancia)** | Bloque repetido que se dibuja una vez y se coloca muchas: más rápido |
| **Previsualizar / Confirmar** | Ver un cambio sin guardarlo / guardarlo como revisión |
| **Norte del plano** | Dirección +Y del dibujo; el norte real se ajusta en el Inspector |

---

Más detalle técnico: [CONFIG.md](CONFIG.md) (configuración), [EXPORT_FORMATS.md](EXPORT_FORMATS.md)
(formatos y coordenadas), [EDICION_IA.md](EDICION_IA.md) (edición con IA) y
[ARCHITECTURE.md](ARCHITECTURE.md) (arquitectura).
