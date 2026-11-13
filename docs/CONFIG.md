# Configuración por proyecto

Cada plano tiene sus propias convenciones (nombres de capa, cómo se indican las alturas, unidades).
Extrude no presupone ninguna: todo se declara en un YAML por proyecto.

## Flujo recomendado

```sh
extrude inspect plano.dxf                       # 1. ver capas, textos, atributos, unidades
extrude init-config plano.dxf -o proyecto.yaml  # 2. borrador adaptado a ese plano
#                                                 3. revisar y ajustar proyecto.yaml
extrude convert plano.dxf -c proyecto.yaml -o out/   # 4. convertir (Fase 3)
```

`init-config` adivina el `kind` por el nombre de la capa (`EDIF*`, `BLOQUE*`, `TORRE*`,
`VIVIEND*`, `UNIFAMILIAR*`, `NAVE*`… → building; `VIAL*`/`CALLE*` → road; `AGUA`/`RIO`/`LAGUNA`
→ water; `VERDE`/`ARBOL` → vegetation; `CURVA`/`NIVEL` → terrain; el resto → ignore). Una capa
de nombre no reconocido no se convierte, pero si contiene contornos cerrados, sombreados o
mallas queda anotada como **pendiente de revisión** (ver [APOYO_Y_COTAS.md](APOYO_Y_COTAS.md)).
La fuente de altura de los edificios se elige con este orden:

1. Atributos de bloque cuyo nombre recuerde a plantas/altura (`PLANTAS`, `ALTURA`, `N_PL`,
   `FLOORS`…) → `height_from: attribute`.
2. Textos con un patrón reconocible → `height_from: text`, eligiendo el patrón con más
   coincidencias: `B+3` (baja + n), números romanos (`IV`, estilo Catastro), `4P`, `12.5m` o un
   número suelto.
3. Si los edificios son bloques (INSERT) → `height_from: block` con un `block_values` provisional
   por tipo de bloque (a ajustar).
4. Polilíneas con cota Z, sin terreno utilizable ni otra fuente → `height_from: elevation`
   (cota de cubierta sobre la cota 0; es una inferencia).
5. Si no encuentra nada → `default_height: 9.0`, que es **provisional**. Con terreno, una Z
   por sí sola no se interpreta como altura.

La base de todo lo que se extruye se deja en `base_auto: true`: la decide Extrude al convertir,
objeto a objeto (ver abajo). El terreno solo cuenta si de verdad se puede triangular con cotas,
no por el nombre de la capa.

Solo cuenta los textos que caen **dentro** de las plantas (no leyendas ni títulos), y un número
suelto únicamente si aparece en al menos la mitad de ellas. Los árboles en bloque se proponen como
volúmenes de 6 m y las capas de "terreno" sin cotas Z se ignoran (no sirven para el TIN).

Cada regla generada lleva comentarios con la evidencia (ejemplos de textos, atributos vistos). Es
un **borrador**: revísalo siempre.

Una sola polilínea acotada no garantiza que toda la capa tenga cotas de base fiables. Para usar
cotas de base verificadas, sustituye `base_auto: true` por `base_from_elevation: true` en esa
regla: las bases explícitas se respetan siempre y, si no coinciden con el terreno, se avisa.

## Estructura

```yaml
units: auto          # auto | mm | cm | m | km | in | ft
recenter: true       # recentrar coordenadas al exportar (imprescindible con UTM)
tolerance: 0.01      # metros
layers:              # lista ordenada: gana la PRIMERA regla cuyo patrón encaje
  - match: "EDIFIC*"
    kind: building
    ...
  - match: "*"       # recomendado: regla final explícita para el resto
    kind: ignore
```

### Opciones globales

| Clave | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `units` | `auto`/`mm`/`cm`/`m`/`km`/`in`/`ft` | `auto` | Unidades del dibujo. `auto` usa `$INSUNITS`; si falta, asume metros y avisa. La salida siempre va en metros. |
| `recenter` | bool | `true` | Resta el centro de la escena al exportar y guarda el desplazamiento en `scene.extras.origin_offset`. |
| `crs` | str | — | Sistema de coordenadas del dibujo, p. ej. `"EPSG:25830"` (ETRS89 / UTM 30N). No reproyecta la geometría: se guarda en `scene.extras` y, si es un EPSG UTM (o hay `pyproj` instalado), se calcula la latitud/longitud del centro para situar el sol del visor. |
| `tolerance` | float > 0 (m) | `0.01` | Cierre de polilíneas casi cerradas, unión de extremos de LINE/ARC, detección de duplicados, área mínima (tolerancia²) y aplanado de curvas. |
| `layers` | lista de reglas | `[]` | Ver abajo. Capas sin regla no se convierten y se listan en el informe. |

### Reglas de capa

| Clave | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `match` | glob | — (obligatorio) | Patrón de nombre de capa, sin distinguir mayúsculas (`*`, `?`, `[abc]`). Para un nombre con esos caracteres, escápalos: `EDIF[*]`. |
| `kind` | `building`/`road`/`water`/`vegetation`/`terrain`/`line`/`ignore` | — (obligatorio) | Qué se genera (ver tabla de kinds). |
| `height_from` | `attribute`/`text`/`elevation`/`block` | — | Fuente de altura por polígono (prioridad 1). |
| `block_values` | `{patrón de bloque: número}` | — | Con `height_from: block`: valor según el nombre del bloque que contiene el polígono (glob, sin distinguir mayúsculas; gana el bloque más interno). Se interpreta según `value_means`. |
| `attribute` | str | — | Etiqueta del ATTRIB (obligatoria con `height_from: attribute`). Sin distinguir mayúsculas. |
| `text_regex` | regex | `(\d+)\s*P` | Con `height_from: text`. El **grupo 1** es el número. Admite decimales con coma y números romanos. Escríbelo entre comillas simples en el YAML. |
| `text_layers` | lista de globs | todas | Capas donde buscar los textos. Recomendado acotarlo para no confundir rótulos. |
| `text_tolerance` | float ≥ 0 (m) | `1.0` | Si dentro del polígono no hay ningún texto (o ATTRIB) válido, se acepta uno situado fuera del contorno a esta distancia como máximo. `0` lo desactiva. Ver abajo. |
| `value_means` | `floors`/`meters` | `floors` | Si el número (texto o atributo) son plantas o metros. |
| `floor_offset` | int | `0` | Plantas a sumar al número leído. Ej.: `B+3` con `floor_offset: 1` → 4 plantas. |
| `floors` | int ≥ 0 | — | Nº de plantas fijo para la capa (prioridad 2). |
| `floor_height` | float > 0 (m) | `3.0` | Altura por planta. |
| `height` | float ≥ 0 (m) | — | Altura fija (prioridad 3). |
| `default_height` | float ≥ 0 (m) | — | Último recurso (prioridad 4). |
| `base_elevation` | float (m) | `0.0` | Cota de la base de la extrusión (o desplazamiento sobre el terreno o sobre la Z de la huella). |
| `base_auto` | bool | `false` | Apoyo automático por objeto: con terreno válido, sobre el terreno bajo su huella (como `base_from_terrain`); sin él, usa la Z de la huella como base y suma `base_elevation`, salvo con `height_from: elevation`, donde Z representa la cubierta. Sin cota utilizable, usa `base_elevation`. Es lo que escribe `init-config`. Excluyente con las dos siguientes. |
| `base_from_elevation` | bool | `false` | La base de cada polígono es su propia cota Z (+ `base_elevation`). Útil cuando las plantas están dibujadas a la cota del terreno. |
| `base_from_terrain` | bool | `false` | Apoya la capa en el terreno (TIN de las capas `kind: terrain`), sumando `base_elevation`. Edificios: usa `terrain_support`. Viales, agua y vegetación: la superficie se adapta a la pendiente. Bloques (árboles…) en terreno casi llano (desnivel ≤ 1 m): base plana, siguen siendo instancias. Excluyente con `base_from_elevation` y `base_auto`. Sin terreno utilizable, usa `base_elevation` y avisa. El borrador de `init-config` propone `base_auto`, no esta opción explícita. |
| `terrain_support` | `foundation` / `minimum` | `foundation` | Para edificios con `base_from_terrain`: `foundation` sitúa la planta en la cota máxima bajo la huella y añade un zócalo hasta el terreno. La cubierta queda horizontal a planta + altura, sin incrementar las plantas asignadas. `minimum` conserva el criterio antiguo: prisma con base en la cota mínima, que puede quedar enterrado en laderas. No afecta a bases explícitas ni al resto de kinds. |
| `detect_holes` | bool | `true` | Un contorno dentro de otro de la misma capa es un patio (par/impar). Desactívalo si en la capa hay volúmenes contenidos en otros (p. ej. torre sobre podio dibujados en la misma capa). Los HATCH siempre usan par/impar. |
| `bottom_cap` | bool | `false` | Generar la cara inferior (solo si el modelo se verá desde abajo; ahorra triángulos). |
| `material` | objeto | según `kind` | `color` (`#rrggbb`), `roughness` y `metalness` (0–1), `opacity` (0–1). |
| `visible` | bool | `true` | Visibilidad inicial de la capa en el visor. |
| `reviewed` | bool | `false` | La clasificación de la capa ya se revisó (p. ej. «No convertir» elegido en el visor): el informe deja de proponerla como pendiente. |

### Cota Z, base y cubierta

Una cota Z en una huella no demuestra por sí sola ni la base, ni la cubierta, ni la altura.
Extrude solo la usa cuando una regla lo dice:

- `height_from: elevation`: la Z es la **cota absoluta de cubierta**. La altura es esa cota
  menos la cota real de la base (terreno incluido); nunca se suman dos cotas absolutas. Si la
  cubierta queda en la base o por debajo, o la Z es 0 (ausencia de cota en un dibujo 2D), no da
  altura y se pasa a la siguiente fuente.
- `base_from_elevation: true`: la Z es la **cota absoluta de la base**.
- `base_auto: true` sin terreno: la Z se toma como base (inferencia, así se informa), salvo
  con `height_from: elevation`, donde sigue representando la cubierta.

Los tres modos de base (`base_auto`, `base_from_elevation`, `base_from_terrain`) son
mutuamente excluyentes. Con todos desactivados se usa la base fija `base_elevation`.
La política automática considera ausencia de cota una Z cuya magnitud no supera `1e-6` m;
sin terreno, esa huella usa `base_elevation`. Con terreno válido, el apoyo automático lo usa
incluso si la huella tiene Z propia; si esa Z no encaja con el terreno, se informa para revisión.
En edificios, `terrain_support` también se aplica cuando el terreno se eligió mediante
`base_auto`. Una altura cero sigue el TIN sin zócalo en ambos modos de apoyo.

### Prioridad de la altura

`height: 0` (o una altura resuelta de cero) representa una superficie sin volumen.
Con `base_from_terrain: true`, también en capas `building`, esa superficie sigue el TIN
con el desplazamiento `base_elevation`, sin generar zócalo ni una cubierta elevada.
Sin apoyo en terreno, permanece en la cota de base indicada. `bottom_cap` no añade una
segunda tapa a estas superficies de espesor cero.

Si una regla usa `base_from_elevation` o `height_from: elevation`, los contornos se procesan
por cota antes de reconstruir líneas, detectar patios y eliminar duplicados. Dos huellas XY
iguales en cotas distintas conservan sus volúmenes; una polilínea y su HATCH en la misma cota
siguen deduplicándose. La cota media se convierte a metros y se agrupa con la precisión de
`tolerance`, como la geometría XY. Los patios se detectan dentro de cada nivel.

El diagnóstico de terreno registra la cobertura de todos los objetos comprobados en
`ground_details` y agrega `terrain_coverage` por capa. Una huella que salga total o
parcialmente del TIN genera un aviso de extrapolación, aunque sus cotas parezcan correctas:
fuera del TIN se usa el punto de terreno más cercano, no topografía medida bajo la huella.

El zócalo automático es una aproximación geométrica, no un cálculo estructural ni una
plataforma de proyecto. No modifica las curvas ni el terreno. Su altura máxima y la cota
de la planta se incluyen en el informe; el GLB exporta `foundation_height` y usa `base`
para la cota de planta (sin sumar el zócalo a `height` o `floors`). Las huellas con zócalos
distintos se exportan como geometría propia, sin compartir una instancia incompatible.
En pendientes fuertes, revisa las alturas supuestas y las cotas reales del proyecto.

Para cada polígono: `height_from` (si encuentra valor) → `floors × floor_height` → `height` →
`default_height`. Si no hay ninguna:

- `building`: el polígono **se descarta** y aparece en el informe como "sin altura".
- `road`, `water`, `vegetation`: superficie plana a `base_elevation` (o sobre el terreno con `base_from_terrain`).

Con `height_from: text`, si dentro del polígono hay varios textos válidos se usa el más cercano a
su punto representativo y el caso se cuenta como "ambiguo" en el informe. Un texto dentro de un
patio no cuenta para el edificio que lo rodea.

Si no hay ningún texto válido dentro, se buscan textos **justo fuera** del contorno, a menos de
`text_tolerance` (los puntos de inserción suelen quedar unos centímetros fuera de la fachada). Se
usa el más cercano al borde, y solo si no está dentro de otro polígono de la capa ni más cerca (o
igual de cerca) de otro: así no se roba el rótulo del edificio de al lado en una medianera. El
informe cuenta estas alturas para que puedas revisarlas.

Con `height_from: attribute` se usan primero los atributos del propio bloque que contiene la
geometría; si la geometría no es un bloque, se buscan bloques-etiqueta (ATTRIB) dentro del polígono.

### Kinds

| Kind | Geometría |
|---|---|
| `building` | Extrusión de polígonos cerrados (con patios) según la altura resuelta. |
| `road`, `water`, `vegetation` | Igual que building, pero sin altura → superficie plana. |
| `terrain` | TIN (Delaunay) con los vértices de polilíneas y POINT de la capa (curvas de nivel, puntos acotados). Si no hay datos suficientes, plano base del tamaño de la escena a `base_elevation`. |
| `line` | Polilíneas como líneas 3D (sin extruir). |
| `ignore` | Nada; se cuenta en el informe. |

La geometría 3D existente (3DFACE, SOLID, MESH, polyface) se incluye tal cual en cualquier capa que
no sea `ignore`.

## Ejemplos de convenciones habituales

```yaml
# Catastro: plantas en romanos ("IV", "II")
- match: "CONSTRU*"
  kind: building
  height_from: text
  text_regex: '^\s*([IVXL]+)\b'
  text_layers: ["TEXTOS_CONSTRU*"]

# Rótulos "B+3" (baja + 3 = 4 plantas)
- match: "EDIFICIOS"
  kind: building
  height_from: text
  text_regex: 'B\s*\+\s*(\d+)'
  floor_offset: 1

# Bloque con atributo ALTURA en metros
- match: "EDIF*"
  kind: building
  height_from: attribute
  attribute: ALTURA
  value_means: meters

# Edificios como bloques sin altura en el dibujo: plantas por tipo de bloque
- match: "-EDIFICIOS"
  kind: building
  height_from: block
  block_values: { "VIV.UNIFAM": 2, "bloque lineal": 5, "torres*": 12 }
  base_from_elevation: true   # la planta está dibujada a la cota de su base

# Polilíneas con elevación = cota de cubierta; terreno a 102,5 m
- match: "CUBIERTAS"
  kind: building
  height_from: elevation
  base_elevation: 102.5
```
