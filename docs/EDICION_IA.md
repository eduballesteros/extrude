# Editar el proyecto con IA (Fase 3)

Escribe libremente qué quieres cambiar («sube la torre central dos plantas», «mueve E-007 10 m al
sur», «ladrillo rojo en todos salvo E-002», «prepara una vista al atardecer») y obtén cambios
**reales y persistentes** en el modelo 3D. La IA interpreta y planifica; Extrude ejecuta
operaciones tipadas, validadas y reversibles. Lo que aún no sabe hacer se declara como pendiente,
nunca como hecho.

```
petición libre → contexto del proyecto (estado actual) → UNA llamada: interpretación + plan
→ validación y resolución local → previsualizar → confirmar (revisión nueva) → deshacer/rehacer
```

Requisitos de origen: [REQUISITOS_MATERIALIDAD_IA.md](REQUISITOS_MATERIALIDAD_IA.md) §5, §9–§11.

## Qué se puede pedir hoy

| Herramienta | Hace | Compatible con | No hace |
|---|---|---|---|
| `material.assign` v1 | Material de la biblioteca en una superficie (fachada, patio, cubierta, zócalo, suelo), con color, rugosidad, **tamaño de pieza** y giro | Objetos que tengan esa superficie | Materiales fuera de la biblioteca |
| `building.set_height` v1 | Regenera la extrusión: mueve la coronación (muros + cubierta) sin escalar planta ni zócalo. Altura absoluta o incremento en m, o plantas × altura de planta | Edificios fusionados de cubierta plana | Cubiertas inclinadas, copias de bloque, áticos |
| `object.move` v1 | Desplaza en planta (m al este / al norte del plano), misma cota | Edificios y copias de bloque de edificio (la copia entera) | Reapoyar sobre terreno irregular; viales |
| `object.rotate` v1 | Gira en planta alrededor del centro del objeto | Edificios y copias de bloque (también escaladas y giradas) | — |
| `object.duplicate` v1 | Copias desplazadas con ID y etiqueta nuevos; heredan materiales | Edificios y copias de bloque | — |
| `view.create` v1 | Vista guardada: encuadre (iso, planta, desde un punto cardinal), centrada en objetos, y hora solar | Cualquiera | — |
| `scene.set_sun` v1 | Hora/fecha del sol del visor | — | — |
| `site.place` v1 | Recursos procedurales genéricos de `site_assets.py` (`site-1`): `tree_round`, `tree_columnar`, `shrub`, `bench`, `street_lamp`, `litter_bin`. Disposición `row` (a lo largo de un objeto, a un lado o a ambos, cada N m), `around` (mirando al centro) o `point`. Apoyo `terrain`, `target_surface` o `elevation` (cota dada). Se colocan como instancias en la capa ENTORNO | Fila o alrededor: un objeto de referencia | Especies o productos concretos (*site.species*). Sin terreno, pide `baseZM` (`needs_input`) |
| `object.delete` v1 | Elimina elementos añadidos (entorno, ventanas, balcones); la fila conserva su índice | Capas `site` y `facade_element` | Borrar edificios del plano |
| `facade.add_windows` v1 | Vidrio (VENTANAS) y carpintería (CARPINTERIAS) **añadidos** sobre la fachada orientada, en el sistema local de la fachada (u, normal), por plantas | Edificios fusionados con altura de planta conocida o dada | Perforar muros (*facade.openings_cut*). Rechaza ventanas duplicadas en la misma fachada y plantas |
| `facade.add_balconies` v1 | Losa y barandilla en plantas ≥ 1 (BALCONES) | Igual que ventanas | Planta baja |
| `building.setback` v1 | Parte el edificio a `startHeightM` y añade un cuerpo superior retranqueado por orientaciones o en todo el perímetro (`shapely` buffer: patios y huellas cóncavas). El ático hereda los materiales | Edificios fusionados de cubierta plana | Rechaza el retranqueo si la planta desaparece |
| `block.make_unique` v1 | Hornea el TRS de **esa** copia en geometría propia, con los mismos IDs y etiquetas; las demás copias no cambian | Copias de bloque | Editar el bloque en todas sus copias (*block.edit*) |
| `building.raise_walls` v1 | Sube muros y cubierta conservando la forma de la cubierta | Cubiertas inclinadas | — |
| `roof.set_ridge` v1 | Fija la altura de cumbrera sobre el alero (el alero no se mueve) | Cubiertas inclinadas | Pendiente por ángulo (*roof.slope_change*) |

Las operaciones que dependen de `block.make_unique` (`dependsOn`) se validan como geometría
propia, y «súbela» abarca todas las piezas de esa copia. Las ventanas y los balcones siguen a su
edificio al moverlo o girarlo; bajar un edificio por debajo de sus elementos alojados es un
conflicto. Los materiales de los elementos añadidos (vidrio, metal y hormigón por defecto) entran
como asignaciones revisadas del plan.

«Dos plantas» usa la **altura de planta configurada** de la capa (`floor_height` del YAML, que
ahora viaja en el GLB). Si no se conoce (GLB antiguo), la operación queda «Falta un dato» y el
usuario la escribe; nunca se inventa. Horas con nombre, reglas explícitas sobre las horas solares
de la ubicación del proyecto: `sunrise` = salida + 20 min, `sunset` = puesta − 20 min, `noon` =
mediodía solar.

**Pendientes (se reconocen y se muestran como capacidad que falta):** huecos reales en los muros
y puertas; zócalos con geometría propia; especies o productos concretos; pendiente de cubierta
por ángulo; caminos y terreno; materiales PBR con mapas propios; materiales nuevos; editar bloques
(todas sus copias); referencias visuales. Peticiones estéticas («aspecto mediterráneo») se traducen en
operaciones concretas marcadas como **supuesto**, desmarcadas por defecto.

## Configuración

Las llamadas las hace `extrude serve`; las claves solo existen en su entorno (nunca en el
navegador, URLs, GLB, `materials.json`, registros ni caché).

La forma más cómoda es el archivo `.env` de la raíz del repositorio (excluido de Git), el
mismo que usa Docker local: una línea `NOMBRE=valor` por clave. `extrude serve` lo busca en
la carpeta actual y sus padres sin salir del repositorio, carga solo las claves de proveedores
y las variables `EXTRUDE_*`, y dice en consola qué nombres ha cargado (nunca los valores). Una
variable ya definida en el entorno manda sobre el archivo; `--env-file RUTA` usa otro archivo
y `--no-env-file` no lee ninguno. Producción no lo usa (ver `deploy/README.md`).

```
ANTHROPIC_API_KEY=sk-ant-…
EXTRUDE_ANTHROPIC_MODEL=claude-sonnet-5-5   # opcional
```

También valen variables de entorno:

```bash
cd pipeline
uv sync --extra ai                      # SDK oficiales: anthropic y openai
export ANTHROPIC_API_KEY=…              # Claude
export OPENAI_API_KEY=…                 # OpenAI
export EXTRUDE_OPENAI_MODEL=…           # obligatorio para OpenAI
uv run extrude serve                    # dice qué proveedores están listos y por qué no
uv run extrude serve --ai-simulated     # + «Simulado»: reglas locales, sin red ni coste
```

| Variable | Por defecto | Uso |
|---|---|---|
| `EXTRUDE_ANTHROPIC_MODEL` / `EXTRUDE_ANTHROPIC_EFFORT` | `claude-opus-5-5` / `low` | Modelo y esfuerzo (el razonamiento cuenta en el límite de salida) |
| `EXTRUDE_OPENAI_MODEL` / `EXTRUDE_OPENAI_REASONING_EFFORT` | — | Modelo (Responses API, `store=False`) y esfuerzo opcional |
| `EXTRUDE_OPENAI_CONTEXT_TOKENS`, `EXTRUDE_OPENAI_MAX_OUTPUT_TOKENS` | — | Límites del modelo de OpenAI, para comprobar que caben los de Extrude |
| `EXTRUDE_AI_MAX_DESCRIPTION` | 4 000 caracteres | Petición máxima |
| `EXTRUDE_AI_MAX_INPUT_TOKENS` | 12 000 | Contexto estimado máximo (el fijo —instrucciones, herramientas y esquema— ronda 6 000) |
| `EXTRUDE_AI_MAX_OUTPUT_TOKENS` | 2 000 | `max_tokens` / `max_output_tokens` |
| `EXTRUDE_AI_MAX_OBJECTS` | 200 | Objetos por consulta |
| `EXTRUDE_AI_SESSION_REQUESTS` | 5 | Consultas por proyecto y sesión antes de pedir confirmación |
| `EXTRUDE_AI_TIMEOUT_S` | 60 | Tiempo límite de la llamada al proveedor |
| `EXTRUDE_AI_PRICES` | — | Tarifas explícitas para una estimación, p. ej. `{"anthropic/claude-opus-5-5": {"inputPerMTok": 4, "outputPerMTok": 20}}` |

## Flujo en el visor (Materiales → Editar proyecto con IA)

1. Elige proveedor y alcance (todo, marcados o una capa) y escribe la petición.
2. **Proponer cambios.** La primera vez con un proveedor se muestra qué se envía, a quién y el
   contenido exacto. *Autorizar y enviar* envía exactamente esa solicitud con su huella
   (`contextHash`): si cambian proveedor, texto, alcance, selección o revisión, la autorización
   se invalida y el servidor rechaza un envío que no coincida (`consent_changed`).
3. Revisa *Así he entendido tu petición*, requisitos, supuestos, operaciones con su estado
   (Lista, Elige objetos, Falta un dato, Bloqueada, No compatible), impacto previsto (p. ej.
   «E-001 12 → 18 m»), objetos resaltados, conflictos y lo que aún no se puede hacer.
4. Resuelve dudas **sin volver a consultar**: *Usar candidatos* o *Usar marcados*; escribe la
   altura de planta si falta. Marcar una operación marca sus dependencias.
5. **Previsualizar marcadas** (GLB temporal + cámara y sol), **Confirmar** (revisión nueva) o
   **Descartar**. **Deshacer / Rehacer** recorren las revisiones del proyecto (también las vistas
   creadas). *Actualizar propuesta* sirve para seguir: «mantén todo, pero cambia la torre».

Escribir, seleccionar, mover la cámara, resolver dudas, previsualizar, confirmar, deshacer o
reabrir **no consultan** a la IA. Cancelar deja de esperar, pero el proveedor puede terminar (y
facturar) la consulta; si termina, queda en caché.

## Qué se envía

Un único mensaje JSON (*Ver datos que se envían*): la petición normalizada; la revisión del
proyecto; los objetos del alcance con referencias cortas (`o1`…): etiqueta, capa, tipo, bloque,
centro relativo en metros (x = este, y = norte del plano), ancho y fondo, altura, altura de
planta, giro, superficies y materiales actuales; las herramientas disponibles y las capacidades
pendientes; los materiales de la biblioteca (con tamaño de pieza); y las asignaciones manuales
del alcance, como protegidas.

**No se envía:** DWG/DXF, GLB, imágenes, coordenadas absolutas, CRS, hashes, rutas, nombres de
archivo, claves ni IDs internos. Capas, bloques y textos del dibujo son datos no confiables, no
instrucciones. Extrude no anonimiza el texto libre.

## Contrato del plan (v2)

El modelo responde con salida estructurada (Anthropic `output_config.format`; OpenAI `text.format`
json_schema estricto) en la que cada operación es una variante por herramienta (`anyOf`). El
servidor valida siempre y devuelve un plan versionado, guardado en `ai/plans/<planId>.json`:

```json
{
  "schemaVersion": 2, "planId": "plan-…", "baseRevision": "rev-003",
  "geometryRevision": "sha256:…", "materialRevision": "mat-002", "catalogVersion": "library-1",
  "provider": "anthropic", "model": "claude-opus-5-5", "promptVersion": "edit-prompt-1",
  "summary": "…", "explicitRequirements": ["…"], "assumptions": ["…"],
  "operations": [{
    "id": "op1", "tool": "building.set_height", "toolVersion": "1",
    "args": {"mode": "delta_floors", "value": 2, "floorHeightM": null},
    "objectIds": ["obj-…"], "pending": null, "dependsOn": [], "basis": "explicit",
    "reason": "…", "status": "ready", "messages": [], "incompatible": [],
    "impact": {"objects": 1, "heights": [{"label": "E-006", "from": 12, "to": 18}]}
  }],
  "pending": [{"id": "q1", "reference": "la torre central", "question": "…",
               "candidateObjectIds": ["obj-…"], "rule": {"kind": "rank", "by": "centrality"}}],
  "conflicts": [{"source": "ai | proposal | manual", "message": "…", "objectIds": [], "operations": []}],
  "unsupported": [{"request": "añade balcones", "capability": "facade.balconies", "reason": "…"}],
  "validation": {"ok": true, "ready": 3, "needs_resolution": 1}
}
```

**Se rechaza entera** (sin cachear ni «reparar» con otra llamada) una respuesta con esquema roto,
herramientas o capacidades inexistentes, argumentos fuera de rango o no finitos, referencias
inventadas, dependencias desconocidas o circulares, aclaraciones inexistentes, URLs o rutas.
Una operación válida pero inaplicable no se presenta como hecha: queda **Bloqueada** (conflicto
declarado por la IA, dos materiales para la misma superficie, dependencia bloqueada) o
**No compatible** (p. ej. subir una copia de bloque), con el motivo por objeto.

**Selectores** (resueltos por Extrude, no por la IA): referencias + capas + bloques + tipos menos
excepciones («todos salvo E-004»). Las referencias vagas van a `pending` con **reglas explícitas
y documentadas** que nunca se aplican sin confirmar: `side` = edificios cuyo centro queda a ese
lado del centro del conjunto (norte = +Y del plano, no geográfico); `rank` = los N primeros por
altura, área en planta o cercanía al centro.

**Prioridades en código:** una asignación manual protegida prevalece y la propuesta contraria se
convierte en conflicto; dos materiales para el mismo objeto y superficie no se resuelven por orden.

## Ejecución y revisiones

- **Aplicar** usa el plan guardado por el servidor y solo las operaciones marcadas, en orden de
  dependencias; exige que se marquen las operaciones de las que dependen.
- **Plan obsoleto:** si el proyecto cambió de revisión desde la propuesta, se rechaza
  (`stale_plan`) indicando los cambios posteriores; hay que pedirla de nuevo.
- **Dudas resueltas y revalidadas:** los objetos elegidos para una aclaración respetan las
  excepciones del encargo («salvo E-001» sigue fuera aunque esté entre los candidatos) y, con
  todos los objetivos ya resueltos, se revalida el conjunto: dos materiales distintos para el
  mismo objeto y superficie o un conflicto declarado sobre esos objetos impiden aplicar
  (`conflict`); una asignación manual protegida se conserva y se indica.
- **Unidades físicas:** mover, girar o duplicar una pieza de un bloque edita su copia entera una
  sola vez (aunque se citen varias piezas) y lo avisa; las piezas tienen etiqueta propia
  («DOBLE #3.2») y al duplicar cada pieza nueva hereda los materiales de su pieza original.
- **Estado de trabajo:** las operaciones encadenadas parten del resultado de las anteriores
  (+3 m y luego +4 m sobre 12 m dan 19 m, también en el impacto previsto).
- **Todo o nada:** se edita una copia en memoria; si una operación falla (p. ej. altura fuera de
  rango), no se escribe nada y el resultado dice qué falló y qué no se aplicó. Las vistas y el
  sol se **preparan y validan en el visor antes** de llamar al servidor (cámara final calculada
  sin animaciones; «atardecer» imposible en esa latitud y fecha se rechaza ahí); el servidor
  exige ese estado preparado (`viewer_unprepared` si falta) y lo guarda con la revisión. Si aun
  así no pudiera aplicarse en el visor, se deshace la revisión recién creada.
- **Validación:** compatibilidad antes; después, vértices finitos, índices en rango, plan de
  materiales válido contra el catálogo nuevo y aviso de solapes en planta.
- **Revisión geométrica nueva** (`sha256` de la anterior + operaciones) para el GLB editado; IDs y
  materiales se conservan; las copias reciben IDs deterministas y etiquetas nuevas (E-008…) y
  heredan los materiales finales del original. Al copiar un edificio se copian también sus
  ventanas y balcones, con sus materiales y vinculados al edificio nuevo.
- **Proyecto persistente** en `out/project.json` (`rev-000` = conversión). Cada confirmación (IA,
  formulario manual o solo vistas) crea `rev-NNN` con su GLB base (`revisions/`) y su plan
  (`materials/mat-NNN.json`); `model.current.glb`, `materials.json` y `model.materialized.glb`
  son el estado servido. Deshacer/rehacer publica otra revisión sin borrar ninguna. Reabrir el
  trabajo (o reiniciar el servidor) recupera el estado sin consultar a la IA. Cada revisión
  guarda también su **estado del visor** (vistas creadas con su estado completo y el sol antes y
  después): deshacer, rehacer y reabrir lo restauran desde el servidor, no desde el navegador.
  Antes de la primera confirmación, el modelo anunciado es `model.glb`.
  La entrega completa se prepara antes de publicarse; si falla la publicación se restauran
  los archivos anteriores. Las teselas tienen nombres por revisión y se conservan las anteriores
  para que un manifest ya descargado siga siendo válido.
- Las exportaciones (PNG, GLB con cámaras, Blender) usan el modelo confirmado; con una
  previsualización activa se bloquean. Las vistas pertenecen al proyecto: recargar el modelo
  editado no las relee ni las repone.

La distribución `around` coloca los elementos a intervalos iguales sobre el perímetro de la
envolvente convexa del objeto, separado por `sideOffsetM`. Así quedan fuera incluso en referencias
alargadas; las concavidades no se siguen. Con `facing: target` miran hacia su centro.

## Consumo

- Una pulsación = como mucho una llamada a un proveedor; sin consultas simultáneas por proyecto
  (409); sin reintentos de los SDK (`max_retries=0`) ni propios, ni ante errores ni truncados.
- Caché por proyecto (`ai/cache/`) con clave sobre todo lo enviado + revisión del proyecto,
  geométrica y de materiales + proveedor, modelo y versiones de instrucciones, esquema y
  biblioteca. *Borrar caché de propuestas* la vacía.
- Si el alcance no cabe se pide acotarlo; no se trunca ni se divide en varias llamadas.
- Tras `EXTRUDE_AI_SESSION_REQUESTS` consultas, cada una más pide confirmación explícita.
- Registro en `ai/log.jsonl`: proveedor, modelo, objetos enviados, duración, estado y consumo
  informado por la API (nunca estimado), sin la petición ni el contexto. Importes solo con
  `EXTRUDE_AI_PRICES`.
- Los pasos adicionales son siempre explícitos (*Actualizar propuesta*); no hay cadenas de
  llamadas automáticas.

## API

| Método y ruta | Función |
|---|---|
| `GET /api/tools` | Registro de herramientas (contratos) y capacidades pendientes |
| `GET /api/ai/providers` | Proveedores, disponibilidad, límites y tarifas |
| `POST /api/jobs/<id>/ai/context` | Lo que se enviaría y su `contextHash` (no llama a la IA) |
| `POST /api/jobs/<id>/ai/propose` | Plan validado (una llamada o caché); `consent` obligatorio |
| `POST /api/jobs/<id>/edit/apply[?preview=1]` | Aplica operaciones marcadas: `{planId, baseRevision, operations, resolutions, inputs}` |
| `GET /api/jobs/<id>/project` | Revisión actual, historial y archivos servidos |
| `POST /api/jobs/<id>/project/{undo,redo}` | Deshacer / rehacer revisiones |
| `POST /api/jobs/<id>/ai/cache/clear` | Borra la caché del trabajo |

## Ampliar con una herramienta nueva

1. Modelo de argumentos pydantic estricto y `ToolSpec` en `edit_tools.py` (compatibilidad por
   objeto en `check`, precondiciones, efectos, previsualizar/confirmar/deshacer).
2. Esquema de salida en `ai_planner.tool_args_schema` (todos los campos requeridos, nulos con
   `anyOf`) e impacto previsto en `_impact`.
3. Ejecución en `project_edit._execute` (servidor, sobre `GlbEditor`) o en
   `viewer/src/materials/viewerOps.ts` (visor), con validación posterior.
4. Quitar la capacidad de `PLANNED`, pruebas de compatibilidad, ejecución, fallo transaccional y
   deshacer, y una fila en la tabla de arriba.

## Pruebas

Contrato con proveedores simulados (sin llamadas facturables): `tests/test_ai_planner.py`
(una llamada, caché, autorización ligada al contenido, privacidad, instancias con escala y giro,
catálogo vacío, respuestas inválidas, conflictos que bloquean, dependencias, reglas, capacidades
pendientes, errores sin reintento), `tests/test_project_edit.py` (petición → plan →
previsualización → confirmación → GLB exportado y validado con Khronos; transacción, dependencias,
subconjuntos, dudas resueltas sin IA, altura de planta dada por el usuario, planes obsoletos,
asignaciones manuales protegidas, deshacer/rehacer y reapertura), `tests/test_ai_providers.py`
(SDK oficiales de Anthropic y OpenAI contra un servidor local: mismo contrato, clave solo en la
cabecera, una sola petición ante errores y truncados), `tests/test_server_ai.py` (flujo HTTP
completo y reinicio del servidor) y `viewer/src/materials/ai.test.ts` (dependencias, autorización,
respuestas tardías, tiempos límite, sol al atardecer). Las regresiones de la auditoría están en
`tests/test_edit_regressions.py` y `viewer/src/materials/viewerOps.test.ts`. Las ampliaciones
están en `tests/test_extensions.py` (cambio medido en el GLB, previsualización, persistencia,
deshacer/rehacer, validación Khronos, simulador), `tests/test_master.py` (GLB comprimidos y
teselas regeneradas) y `tests/test_shared.py` (modo multiusuario).

Esto prueba el **contrato**, no la calidad de interpretación: medirla exige evaluar con modelos
reales un conjunto de peticiones, lo que tiene coste y requiere autorización.

## Limitaciones

- No se ha hecho ninguna llamada real a Claude ni a OpenAI.
- El proveedor simulado es un intérprete de reglas para probar el flujo, no IA.
- Mover no reapoya sobre terreno irregular. Una copia de bloque cambia de altura solo después de
  independizarla. En cubiertas inclinadas se suben los muros o se fija la cumbrera, pero no se
  cambia la pendiente por ángulo.
- Las ventanas son elementos añadidos: el muro no se perfora.
- El tamaño de pieza se traduce al mosaico de textura (`tileSizeM` = pieza × piezas por mosaico).
- El consentimiento y el contador de consultas viven en el navegador y en la memoria del servidor;
  en modo `--shared` hay además un límite de consultas por usuario y hora, pero no límites de
  gasto por organización. Cancelar no detiene el cálculo remoto.
