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

`init-config` adivina el `kind` por el nombre de la capa (`EDIF*` → building, `VIAL*`/`CALLE*` →
road, `AGUA`/`RIO` → water, `VERDE`/`ARBOL` → vegetation, `CURVA`/`NIVEL` → terrain; el resto →
ignore) y la fuente de altura de los edificios con este orden:

1. Atributos de bloque cuyo nombre recuerde a plantas/altura (`PLANTAS`, `ALTURA`, `N_PL`,
   `FLOORS`…) → `height_from: attribute`.
2. Textos con un patrón reconocible → `height_from: text`, eligiendo el patrón con más
   coincidencias: `B+3` (baja + n), números romanos (`IV`, estilo Catastro), `4P`, `12.5m` o un
   número suelto.
3. Polilíneas con cota Z → `height_from: elevation`.
4. Si no encuentra nada → solo `default_height: 9.0`.

Cada regla generada lleva comentarios con la evidencia (ejemplos de textos, atributos vistos). Es
un **borrador**: revísalo siempre.

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
| `tolerance` | float > 0 (m) | `0.01` | Cierre de polilíneas casi cerradas, unión de extremos de LINE/ARC, detección de duplicados, área mínima (tolerancia²) y aplanado de curvas. |
| `layers` | lista de reglas | `[]` | Ver abajo. Capas sin regla no se convierten y se listan en el informe. |

### Reglas de capa

| Clave | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `match` | glob | — (obligatorio) | Patrón de nombre de capa, sin distinguir mayúsculas (`*`, `?`, `[abc]`). Para un nombre con esos caracteres, escápalos: `EDIF[*]`. |
| `kind` | `building`/`road`/`water`/`vegetation`/`terrain`/`line`/`ignore` | — (obligatorio) | Qué se genera (ver tabla de kinds). |
| `height_from` | `attribute`/`text`/`elevation` | — | Fuente de altura por polígono (prioridad 1). |
| `attribute` | str | — | Etiqueta del ATTRIB (obligatoria con `height_from: attribute`). Sin distinguir mayúsculas. |
| `text_regex` | regex | `(\d+)\s*P` | Con `height_from: text`. El **grupo 1** es el número. Admite decimales con coma y números romanos. Escríbelo entre comillas simples en el YAML. |
| `text_layers` | lista de globs | todas | Capas donde buscar los textos. Recomendado acotarlo para no confundir rótulos. |
| `value_means` | `floors`/`meters` | `floors` | Si el número (texto o atributo) son plantas o metros. |
| `floor_offset` | int | `0` | Plantas a sumar al número leído. Ej.: `B+3` con `floor_offset: 1` → 4 plantas. |
| `floors` | int ≥ 0 | — | Nº de plantas fijo para la capa (prioridad 2). |
| `floor_height` | float > 0 (m) | `3.0` | Altura por planta. |
| `height` | float ≥ 0 (m) | — | Altura fija (prioridad 3). |
| `default_height` | float ≥ 0 (m) | — | Último recurso (prioridad 4). |
| `base_elevation` | float (m) | `0.0` | Cota de la base de la extrusión. Con `height_from: elevation`, altura = Z − base. |
| `detect_holes` | bool | `true` | Un contorno dentro de otro de la misma capa es un patio (par/impar). Desactívalo si en la capa hay volúmenes contenidos en otros (p. ej. torre sobre podio dibujados en la misma capa). Los HATCH siempre usan par/impar. |
| `bottom_cap` | bool | `false` | Generar la cara inferior (solo si el modelo se verá desde abajo; ahorra triángulos). |
| `material` | objeto | según `kind` | `color` (`#rrggbb`), `roughness` y `metalness` (0–1), `opacity` (0–1). |
| `visible` | bool | `true` | Visibilidad inicial de la capa en el visor. |

### Prioridad de la altura

Para cada polígono: `height_from` (si encuentra valor) → `floors × floor_height` → `height` →
`default_height`. Si no hay ninguna:

- `building`: el polígono **se descarta** y aparece en el informe como "sin altura".
- `road`, `water`, `vegetation`: superficie plana a `base_elevation`.

Con `height_from: text`, si dentro del polígono hay varios textos válidos se usa el más cercano a
su punto representativo y el caso se cuenta como "ambiguo" en el informe. Un texto dentro de un
patio no cuenta para el edificio que lo rodea.

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

# Polilíneas con elevación = cota de cubierta; terreno a 102,5 m
- match: "CUBIERTAS"
  kind: building
  height_from: elevation
  base_elevation: 102.5
```
