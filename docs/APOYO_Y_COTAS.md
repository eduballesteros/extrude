# Apoyo sobre el terreno, cotas y revisión del proyecto

Cómo decide Extrude dónde apoya cada objeto, qué acredita cada comprobación y cómo se revisa o
se repara un proyecto desde el visor. La política es general: no depende de nombres de capa,
coordenadas ni cotas de ningún plano concreto.

## Qué distingue Extrude

| Dato | De dónde sale | Fiabilidad que se informa |
|---|---|---|
| Altura relativa | Rótulo, atributo, tipo de bloque, `floors`, `height` | **Acreditada** (`verified`) |
| Cota absoluta de cubierta | `height_from: elevation` (Z de la huella) | **Inferida** (`inferred`): altura = cubierta − base real |
| Cota absoluta de base | `base_from_elevation` (explícita) | Se respeta; si no coincide con el terreno, se avisa |
| Base sobre el terreno | `base_from_terrain` o `base_auto` con terreno válido | `terrain`, o `terrain_extrapolated` fuera del TIN |
| Sin información | `default_height` | **Provisional**: no es un dato del plano |
| Geometría 3D existente | Mallas y caras 3D | Se conserva tal cual; no se reapoya |

Cada objeto lleva su procedencia en el GLB (`height_origin`, `height_confidence`,
`base_origin`) y en el informe; el visor la muestra al pulsarlo.

## Política de apoyo automático (`base_auto`)

Se decide al convertir, por objeto:

1. Hay un terreno **válido** → el objeto se apoya en el terreno bajo su propia huella. Edificios:
   planta horizontal en la cota máxima y zócalo hasta el terreno; la altura se mide desde la
   planta. Viales, agua y vegetación siguen la pendiente. Nunca se desplaza una capa entera.
2. No hay terreno válido → la base es la cota Z de la huella si la tiene (inferencia); si no, 0.
3. Una Z que no coincide con el terreno bajo la huella no se interpreta: se cuenta y se avisa.

Las bases explícitas (`base_from_elevation`, `base_elevation`) y `base_from_terrain` no cambian.

## Terreno realmente utilizado

Un terreno es válido solo si se puede triangular y aporta cotas. No basta el nombre de la capa
ni que alguna coordenada tenga Z:

- todas las cotas a 0 → no aporta topografía;
- menos de tres puntos o puntos alineados → no se puede triangular;
- varias muestras en el mismo XY con Z distinta → se avisa (el TIN usa una);
- varias capas de terreno → el apoyo usa su superficie conjunta y se avisa de que la
  triangulación dibujada de cada capa puede diferir entre capas;
- curvas, puntos acotados y mallas 3D (3DFACE, MESH) alimentan la misma superficie de apoyo.

El informe (`terrain`) dice el estado, el motivo, las capas y el rango de cotas. Las huellas
total o parcialmente fuera del TIN se apoyan en el punto más cercano y se marcan como
extrapoladas. Estar dentro de la envolvente no garantiza precisión local: Delaunay no impone
las curvas como líneas de rotura.

## Diagnóstico de contacto

Se comprueba la geometría generada, no el modo declarado. Tolerancia: 1 m. Categorías
excluyentes por objeto (`ground_check` y `ground_details`):

| Categoría | Significa |
|---|---|
| `floating` | Toda la base queda más de 1 m por encima del terreno |
| `partial_gap` | Toca en parte de la huella y queda en el aire en el resto |
| `intersecting` | El terreno supera la base sin llegar a la cubierta |
| `partially_buried` | El terreno supera la cubierta en parte de la huella |
| `buried` | La cubierta queda bajo todo el terreno |
| `unverified` | No se pudo medir el contacto en la malla |
| `extrapolated` (cobertura) | La huella sale del TIN |

**Qué acredita:** los puntos muestreados (contorno, cortes con las aristas del TIN y vértices
interiores). **Qué no:** no es una validación estructural, no detecta errores menores de 1 m ni
dice si la implantación (plataformas, accesos, cota de planta) es correcta. Un zócalo de más de
3 m se señala para revisión aunque el contacto esté resuelto.

`summary` reúne lo convertido, lo descartado, las capas ignoradas y las **pendientes de
revisión**: «0 flotantes» habla solo de lo convertido.

## Capas pendientes de revisión

Una capa no convertida con contornos cerrados, sombreados o mallas se lista en
`pending_layers`. No se convierte por su cuenta: puede ser edificación, parcelas o un cajetín.
Se resuelve en el visor (Capas → Revisión del proyecto) eligiendo su tipo; «No convertir» la
marca como revisada (`reviewed: true`). Un edificio clasificado así nace con 9 m provisionales.

## Revisar y reparar un proyecto desde el visor

Pestaña **Capas → Revisión del proyecto**, para proyectos nuevos y antiguos (se abren desde
«Proyectos en el servidor» en la pantalla de inicio, sin volver a subir el plano):

- **Apoyo en el terreno:** capas cuya base no está sobre el terreno. Las de base fija con
  objetos mal apoyados vienen marcadas; las de base explícita se pueden marcar a mano.
- **Capas sin convertir con geometría:** se elige su tipo.
- **Aplicar y reconvertir:** el servidor añade los ajustes al YAML en un bloque gestionado (la
  regla original, sus comentarios y las demás reglas no se tocan), reconvierte el plano
  guardado y solo sustituye el resultado si termina bien.
- **Recuperar el resultado anterior:** modelo, YAML, materiales y ediciones de antes de la
  última reconversión (se guarda uno).

Al reparar el apoyo de un borrador generado, la cota Z deja de tomarse como altura
(`z_height: ignore`): era una suposición del borrador antiguo. En una configuración escrita a
mano, `height_from: elevation` se conserva y pasa a ser la cubierta absoluta sobre la base real.

Los materiales y ediciones confirmados parten de la geometría anterior: el modelo reconvertido
no los tiene, y se recuperan con «Recuperar el resultado anterior».

| Método y ruta | Función |
|---|---|
| `GET /api/projects` | Proyectos convertidos que conserva el servidor |
| `GET /api/jobs/<id>/review` | Apoyo por capa, capas pendientes, terreno, ajustes y si se puede deshacer |
| `POST /api/jobs/<id>/reconvert` | `{heights?, adjust?: {capa: {height?, support?, kind?}}}` |
| `POST /api/jobs/<id>/restore` | Recupera el resultado anterior |
| `GET /api/jobs/<id>/result` | Resultado vigente, con su `revision` |

## Que el visor muestre el resultado correcto

- `GET /api/health` incluye `build`: huella del código que el proceso cargó al arrancar, la del
  disco y `stale` si difieren. Un servidor arrancado antes de un cambio sigue con el código
  antiguo: el visor lo avisa y hay que reiniciarlo. Recargar la página no lo arregla.
- Cada resultado lleva su `revision` (la `geometry_revision` del GLB y del informe). Tras
  cargar, el visor comprueba que el modelo es de esa revisión; si no, o si la carga falla,
  muestra «Generado pero no cargado» y permite reintentar sin volver a convertir.
- Los GLB y planes se sirven con `Cache-Control: no-store`.
- Reabrir un proyecto carga el resultado vigente del servidor.

## Limitaciones

- Las alturas provisionales siguen siéndolo: apoyar un edificio no acredita su altura.
- No se deduce una cota de cubierta de una Z salvo que la regla lo pida.
- El DWG necesita un conversor externo; los sólidos ACIS no se convierten.
- Las mallas 3D de origen no se reapoyan.
- La clasificación por nombre usa vocabulario en español e inglés; lo demás queda pendiente.
- Solo se guarda un resultado anterior por proyecto.
