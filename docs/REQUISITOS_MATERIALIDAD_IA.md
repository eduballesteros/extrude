# Hoja de requisitos: DWG + descripción → modelo 3D materializado

**Producto:** Extrude  
**Versión del documento:** 1.0 · 11 de noviembre de 2026  
**Estado:** Fases 0, 1 y 2 implementadas: IDs persistentes, superficies semánticas,
`objects.json`, esquema y validación de `materials.json`, UV en metros, biblioteca de 12 presets
con texturas procedurales, `extrude materialize` y pestaña Materiales del visor (asignación
manual, previsualizar, descartar, deshacer, confirmar revisiones). Fases 3–5 pendientes
(interpretación del prompt con IA, exportaciones coherentes, endurecimiento).  
**Entrega propuesta:** primera versión con biblioteca PBR controlada, revisión de asignaciones y GLB autocontenido.

## 1. Objetivo y resultado esperado

Permitir que un arquitecto o urbanista cargue un DWG/DXF, describa los materiales y el aspecto
del proyecto, revise qué edificios ha interpretado la herramienta y obtenga un modelo 3D con
los materiales incorporados. Después podrá cambiar de cámara, iluminación y encuadre para
crear vistas coherentes, o exportar el modelo a Blender.

El resultado principal será **un modelo materializado reutilizable**, acompañado de sus
asignaciones editables. Las imágenes se renderizarán a partir de ese modelo.

**Ejemplo de entrada:**

> Los edificios del norte tienen fachada de ladrillo claro. La torre central lleva hormigón
> visto y vidrio ligeramente azulado. Pavimentos de piedra gris, cubiertas de grava y
> vegetación mediterránea. El conjunto debe tener un aspecto sobrio y luminoso.

**Resultado esperado:** selección verificable de los edificios afectados, fachadas y cubiertas
con materiales de escala correcta, pavimentos materializados y un aviso sobre cualquier parte
no realizable con la geometría disponible. Si el DWG no contiene ventanas, el sistema no debe
presentar una fachada lisa azulada como si hubiera creado carpinterías y huecos reales.

### Indicadores de éxito

- El usuario reconoce qué material tiene cada edificio y por qué se le asignó.
- Las ambigüedades se resuelven antes de confirmar los cambios afectados.
- Dos vistas del mismo proyecto mantienen las mismas asignaciones y escalas de textura.
- El GLB puede abrirse en otro equipo, sin conexión al servidor, con sus texturas.
- Reaplicar una asignación guardada no requiere volver a consultar a la IA.
- Materializar conserva dimensiones, alturas, patios, posición geográfica y apoyo del modelo.

## 2. Alcance de la primera versión

| Incluido en el MVP | Desarrollo posterior |
|---|---|
| DWG mediante los conversores disponibles y DXF directo, conservando el flujo YAML actual | Nuevos formatos de entrada o reconstrucción desde imágenes |
| Edificios extruidos: fachadas exteriores, fachadas de patios, cubierta y zócalo automático | Generación de ventanas, balcones, carpinterías, lamas o retranqueos |
| Materiales para viales, superficies de agua y vegetación ya modelada | Distribución de vegetación, especies, mobiliario y paisajismo generado |
| Instrucciones en español, por capa, bloque, etiqueta o edificio seleccionado | Descripciones de fachada con composición arquitectónica generativa |
| Biblioteca de materiales existentes, con parámetros y texturas PBR | Generación de texturas mediante IA y subida de texturas propias |
| Corrección manual de asignación, color, escala y orientación | Variantes de proyecto y comparación de materialidad entre versiones |
| Un modelo de salida GLB único, PNG, vistas y exportación coherente a Blender | Materialización y paquetes de exportación de conjuntos teselados |
| Paleta de materiales y ajustes de presentación compatibles con el visor actual | Promesas de fotorrealismo independiente del detalle del plano |

Los bloques repetidos e instanciados forman parte del MVP. Los modelos teselados mostrarán una
limitación explícita y ofrecerán convertir a GLB único si el tamaño lo permite. No se ofrecerá
una exportación de teselas incompleta como si contuviera todo el proyecto.

Las mallas 3D de origen sin clasificación semántica podrán conservarse o recibir un material
único manual. La asignación automática diferenciada de fachada/cubierta/zócalo exige que esas
superficies estén identificadas de forma fiable.

## 3. Usuarios y casos de uso

| Usuario | Necesidad |
|---|---|
| Arquitecto | Asignar una intención material y obtener varias vistas sin repetir el trabajo |
| Urbanista | Diferenciar conjuntos, usos y tratamientos del espacio público |
| Visualizador | Recibir un GLB con geometría, materiales y texturas que pueda continuar en Blender |

### Historias principales

1. Como arquitecto, quiero describir la materialidad con lenguaje habitual y revisar su aplicación.
2. Como urbanista, quiero seleccionar varios edificios y aplicarles el mismo tratamiento.
3. Como usuario, quiero corregir una interpretación sin volver a convertir todo el DWG.
4. Como visualizador, quiero exportar exactamente la materialidad visible en Extrude.
5. Como usuario, quiero saber qué parte del resultado procede del dibujo y qué parte es una suposición.

## 4. Flujo de usuario

1. **Cargar plano y configuración.** Se convierte la geometría y se muestra el resumen de
   calidad existente. La materialización puede continuar con avisos; los errores que impidan
   generar geometría válida deben resolverse antes.
2. **Identificar el proyecto.** Previsualización con edificios seleccionables y etiquetas
   visibles, por ejemplo E-001, E-002. Se muestra la orientación del plano.
3. **Describir materiales.** Campo de texto con ejemplos y acceso a la biblioteca disponible.
4. **Interpretar.** El sistema propone una tabla de asignaciones y señala los objetos sobre el
   modelo: edificio/grupo, superficie, material, motivo y estado de resolución.
5. **Resolver dudas.** El usuario elige entre candidatos, selecciona edificios directamente o
   modifica la instrucción. Las referencias sin resolver permanecen pendientes.
6. **Previsualizar.** Se aplican cambios temporales, separados del último estado confirmado.
   Se pueden corregir y comparar con el estado anterior.
7. **Confirmar.** Se guarda una revisión de materialidad y se actualiza el modelo exportable.
8. **Crear vistas.** Cambiar cámara, sol y encuadre conserva la materialidad confirmada.
9. **Exportar o continuar.** GLB materializado, PNG, vistas y Blender; las asignaciones quedan
   disponibles para reabrir el proyecto.

No se exigirá que el usuario conozca handles DXF, índices de primitivas o detalles de glTF.
Esos datos permanecerán disponibles en diagnóstico y trazabilidad.

## 5. Identificación y resolución de edificios

### 5.1 Identidad técnica y etiquetas de usuario

- Cada objeto tendrá un identificador estable dentro de una revisión geométrica.
- Ese identificador será independiente del material, tesela, primitiva de exportación e índice
  temporal de selección. No se utilizará `_OBJECT_ID` como identidad persistente del proyecto.
- La procedencia incluirá el dibujo, la ruta de bloques/INSERT, handles y componentes generados.
  Las entidades sin handle o las huellas procedentes de varios segmentos también deben resolverse.
- Se distinguirán identidad del proyecto, revisión geométrica y revisión de materialidad.
- E-001 será una etiqueta amigable asociada a esa identidad, no un identificador deducido por la IA.
- No se garantiza identidad automática tras editar o volver a guardar el DWG. Si cambia la
  revisión geométrica, se validarán las correspondencias y se mostrarán las no resueltas.

### 5.2 Referencias aceptadas

| Referencia en la descripción | Comportamiento esperado |
|---|---|
| «La capa TORRES» | Seleccionar los objetos de la capa y mostrar su número |
| «Los bloques VIVIENDA_A» | Resolver las instancias concretas del bloque |
| «E-004 y E-007» | Asignar a esos objetos, sin inferencia espacial |
| «El edificio que he seleccionado» | Usar la selección actual y fijar sus IDs en el plan |
| «La torre central» | Proponer candidatos mediante altura y posición; confirmar si no hay una referencia inequívoca |
| «Los edificios del norte» | Mostrar la región y la selección interpretadas; pedir delimitación si el término no define un conjunto claro |

El norte por defecto será el **norte del plano, +Y del dibujo**. Cualquier ajuste de norte del
proyecto se mostrará y se usará de forma coherente. No se confundirá automáticamente el norte
de la cuadrícula con norte geográfico. Las referencias espaciales se resolverán sobre el dibujo
en metros, antes del recentrado y del cambio de ejes de exportación.

Una vez revisada una selección espacial, el plan persistido guardará los IDs concretos
resueltos. Reabrirlo no dependerá de volver a interpretar «central» o «norte».

### 5.3 Conflictos y prioridades

1. Una edición manual confirmada tiene prioridad sobre una interpretación nueva.
2. Dentro de una propuesta, una asignación explícita por objeto prevalece sobre una de grupo
   o capa si no contradice una edición manual protegida.
3. Una regla por superficie prevalece sobre un material general del mismo objeto.
4. Dos instrucciones incompatibles para el mismo objeto y superficie generan un conflicto;
   el orden del texto no lo resolverá silenciosamente.
5. Las superficies sin asignación conservan su material anterior o el predeterminado de la capa.
6. «A todos salvo…» deberá mostrar tanto incluidos como excluidos antes de confirmar.

## 6. Requisitos funcionales

**P0:** obligatorio para publicar el MVP. **P1:** siguiente incremento, sin bloquear el MVP.

| ID | Prioridad | Requisito | Criterio de aceptación |
|---|---|---|---|
| RF-01 | P0 | Admitir DWG/DXF y configuración geométrica existente | El mismo archivo/configuración genera la misma revisión geométrica antes de materializar |
| RF-02 | P0 | Permitir describir materiales en español | El ejemplo de este documento produce una propuesta y enumera sus partes no realizables |
| RF-03 | P0 | Crear un catálogo seleccionable de objetos | Cada edificio visible se puede identificar desde el modelo y desde una lista |
| RF-04 | P0 | Asignar por capa, bloque, etiqueta y selección | Las cuatro rutas producen asignaciones a IDs existentes |
| RF-05 | P0 | Resolver referencias espaciales de manera revisable | El sistema destaca la selección interpretada y no confirma referencias ambiguas |
| RF-06 | P0 | Producir un plan estructurado validado | No se aceptan IDs, superficies, parámetros o materiales inexistentes |
| RF-07 | P0 | Mostrar conflictos y referencias sin resolver | Cada caso tiene una acción: seleccionar, reformular, sustituir u omitir |
| RF-08 | P0 | Ofrecer previsualización y confirmación | Cancelar la previsualización restaura el estado confirmado completo |
| RF-09 | P0 | Editar asignaciones manualmente | Cambiar el material de E-004 no cambia otros edificios ni instancias |
| RF-10 | P0 | Diferenciar fachada, patio, cubierta y zócalo | Cambiar cubierta afecta solo a su superficie; el zócalo conserva su asignación propia |
| RF-11 | P0 | Aplicar texturas con escala física | Una textura que representa 2 m se repite cada 2 m, también en modelos UTM o dibujados en mm |
| RF-12 | P0 | Ajustar color, rugosidad, escala y orientación | Los cambios se ven en el visor, se guardan y se conservan al exportar |
| RF-13 | P0 | Mantener las asignaciones entre vistas | Guardar/restaurar cámara y sol no sustituye la materialidad confirmada |
| RF-14 | P0 | Deshacer y rehacer cambios de materialidad | Se recuperan al menos las últimas 20 operaciones de edición de la sesión |
| RF-15 | P0 | Guardar y reabrir asignaciones | Con los mismos archivos y revisiones se reabre el proyecto sin nueva llamada a la IA |
| RF-16 | P0 | Exportar GLB autocontenido | Un segundo equipo abre el GLB sin pedir texturas al servidor de Extrude |
| RF-17 | P0 | Exportar el estado materializado actual | PNG, GLB y GLB con cámaras usan la revisión confirmada, incluidas correcciones manuales |
| RF-18 | P0 | Mantener el flujo Blender para GLB único | El script importa el GLB materializado y conserva los materiales y las cámaras |
| RF-19 | P0 | Informar de limitaciones de geometría | Una petición de ventanas, carpinterías inexistentes o especies vegetales no se considera completada mediante una textura genérica |
| RF-20 | P0 | Informar de progreso y permitir recuperación | Se distinguen interpretación, revisión, preparación de texturas y exportación; un fallo permite reintentar sin perder el modelo anterior |
| RF-21 | P0 | Aplicar cambios confirmados de forma atómica | Ninguna cancelación deja una mezcla parcial de materiales nuevos y antiguos |
| RF-22 | P0 | Ofrecer uso manual sin IA disponible | Sin credenciales, red o servicio, se pueden elegir materiales y exportar |
| RF-23 | P0 | Registrar trazabilidad | Cada asignación indica objeto, superficie, material, origen manual/IA y revisión |
| RF-24 | P0 | Distinguir materialidad y presentación | «Aspecto luminoso» puede proponer paleta/ajustes; una petición de luz o fecha se muestra aparte y no altera la geometría |
| RF-25 | P1 | Reutilizar una paleta en otro proyecto | Las reglas se revisan contra el nuevo catálogo, sin trasladar IDs como si fueran equivalentes |
| RF-26 | P1 | Exportar un paquete de proyecto portable | Incluye geometría, asignaciones, biblioteca utilizada y vistas para continuar la edición en otro equipo |

## 7. Biblioteca de materiales y parámetros

### 7.1 Catálogo inicial propuesto

Un mínimo de 12 presets: ladrillo claro, ladrillo rojo, hormigón visto, revestimiento blanco,
piedra gris, asfalto, grava de cubierta, madera exterior, metal oscuro, vidrio azulado,
vegetación genérica y agua.

Cada preset deberá tener:

- ID y versión estables, nombre, familia, descripción y miniatura.
- Color base y factores PBR; texturas de color, normal y rugosidad cuando aporten valor.
- Escala física documentada: ancho y alto reales que representa la textura.
- Espacios de color correctos: color base en sRGB; mapas de datos en espacio lineal.
- Licencia, procedencia y permiso de redistribución, incluyendo exportación dentro del GLB.
- Variante de respaldo cuando un recurso opcional no esté disponible, con aviso visible.

La IA elegirá materiales del catálogo y ajustará parámetros permitidos. Si pide un material
no disponible, propondrá una alternativa identificada como tal o dejará la asignación pendiente.
No descargará recursos arbitrarios a partir de URLs producidas por el modelo.

### 7.2 Límites de representación

- El normal map simula relieve en iluminación; no añade volumen, silueta ni despiece geométrico.
- El vidrio del MVP será una aproximación mediante transparencia y rugosidad. No se prometerá
  refracción física ni un interior que no exista.
- «Carpinterías negras» se aplicará a superficies existentes identificadas como carpintería.
  Sin ellas, se notificará la carencia.
- «Vegetación mediterránea» podrá definir una paleta para geometría vegetal existente. La
  generación de árboles o la identificación de especies queda fuera de esta versión.
- Los cambios de exposición, cielo o sol se guardan como presentación/vista; no se hornean en
  las texturas PBR del proyecto.

## 8. Requisitos de geometría, UV y render

| ID | Requisito técnico verificable |
|---|---|
| RG-01 | Etiquetar las superficies durante la construcción geométrica. No reconstruir fachada/cubierta únicamente mediante una heurística de normales al final |
| RG-02 | Separar semánticamente el zócalo hasta la cota de planta; las paredes actuales que lo integren deben poder dividirse sin cambiar la envolvente del edificio |
| RG-03 | Generar UV de fachada con distancia horizontal sobre el contorno y altura en metros; mantener las hiladas horizontales en esquinas y paños con distinta orientación |
| RG-04 | Generar UV de cubierta/pavimento con proyección y orientación documentadas; no rellenar patios ni distorsionar áreas con huecos |
| RG-05 | Resolver normales y tangentes compatibles con los normal maps exportados; verificar la orientación tras el paso DWG Z-up → glTF Y-up |
| RG-06 | Aplicar la escala material en unidades de mundo, incluida una instancia escalada; un INSERT al doble de tamaño no debe duplicar el tamaño del ladrillo |
| RG-07 | Agrupar geometría por capa y material cuando sea compatible. Evitar crear una malla/draw call por edificio como solución general |
| RG-08 | Reagrupar instancias por geometría y asignación material. Un cambio en una copia no debe teñir o modificar todas las demás |
| RG-09 | Conservar selección y trazabilidad al dividir primitivas. Los índices locales de vértices/objetos no sustituyen el ID persistente |
| RG-10 | Conservar CRS, origin_offset, alturas, planta, cubierta, patios y apoyo. Se permite duplicar vértices por costuras UV o subdividir caras coplanares; no cambiar la forma |

**Tolerancia de conservación propuesta:** diferencia máxima de envolvente y distancias de
referencia de 1 mm en las fixtures de validación, evaluada en metros y separando el error de
compresión. La comparación usará superficies/medidas; no exigirá el mismo número de vértices
o triángulos, porque las costuras UV pueden requerir duplicarlos.

## 9. Contrato del plan de materialidad

El plan será un archivo versionado independiente del prompt. La descripción original y la
respuesta de la IA serán evidencia, no la única forma de reconstruir las asignaciones.

Ejemplo conceptual de formato; debe cerrarse como esquema antes de implementar la API:

```json
{
  "schemaVersion": 1,
  "projectId": "proyecto-a",
  "geometryRevision": "sha256:...",
  "materialRevision": "mat-003",
  "catalogVersion": "library-1",
  "assignments": [
    {
      "objectIds": ["obj-004", "obj-007"],
      "surface": "facade_exterior",
      "materialId": "brick_light",
      "materialVersion": "1",
      "parameters": {
        "tileSizeM": [2.0, 2.0],
        "rotationDeg": 0,
        "roughness": 0.85
      },
      "origin": "ai_reviewed",
      "reason": "Selección norte confirmada por el usuario"
    }
  ]
}
```

Superficies mínimas: `facade_exterior`, `facade_courtyard`, `roof`, `foundation`, `ground` y
`generic`. Solo estarán disponibles las presentes en cada objeto.

El borrador de IA tendrá una lista separada de referencias pendientes y conflictos. El plan
confirmado contendrá asignaciones resueltas; una omisión voluntaria quedará registrada como
omisión, sin declarar que se ha cumplido esa parte de la descripción.

Se validarán todos los IDs, versiones, enumeraciones, rangos, unidades y valores finitos.
Las preferencias manuales protegidas y las reglas de prioridad se resolverán en código.
La repetibilidad se exige al aplicar un plan confirmado, aunque dos interpretaciones nuevas
del mismo texto puedan producir propuestas distintas.

## 10. Persistencia, vistas y exportación

### Estado del proyecto

- Mantener geometría, materialidad confirmada y estado de presentación como partes distintas.
- Guardar el plan en `materials.json`, con referencias a la revisión geométrica y al catálogo.
- Una revisión material confirmada deberá ser inmutable; las ediciones crean una nueva revisión.
- El MVP usará una materialidad global del proyecto. Restaurar una cámara/vista no recuperará
  materiales antiguos de forma implícita.
- Las sobreescrituras de color de vistas anteriores deben migrarse o ignorarse de forma
  explícita al activar materiales. No pueden borrar o teñir silenciosamente las asignaciones.
- La persistencia interna no se basará solo en el nombre del GLB. Si se recarga otra geometría
  con el mismo nombre, se detectará la diferencia de revisión.
- La copia portable editable podrá incorporarse en P1; en el MVP, el GLB final ya deberá ser
  portable como modelo visual y `materials.json` exportable como registro de asignaciones.

### GLB y Blender

- Las texturas utilizadas se incorporarán al GLB, con materiales PBR y UV válidos.
- El GLB materializado será la fuente canónica de la exportación con cámaras y luces.
  No se parchearán los bytes del GLB inicial sin materiales como si fueran la versión actual.
- El script Blender apuntará al nombre del GLB materializado entregado y explicará qué archivos
  deben estar juntos. No volverá a generar los materiales mediante otra interpretación de IA.
- La comparación visor/Blender comprobará asignación, escala, orientación y parámetros.
  La iluminación y el tone mapping pueden producir imágenes distintas; no se exige igualdad
  de píxeles entre motores.
- `views.json` identificará la revisión material global necesaria para reproducir el proyecto.
  Su importación avisará si esa revisión no coincide o no está disponible.

## 11. IA, privacidad y seguridad

| ID | Requisito |
|---|---|
| RS-01 | El modelo interpretará la intención y propondrá un plan; el motor geométrico y material ejecutará operaciones validadas |
| RS-02 | No se ejecutará código, comandos de shell ni scripts generados a partir del prompt o del contenido del DWG |
| RS-03 | Los nombres de capa, textos y atributos del dibujo se tratarán como datos, nunca como instrucciones del sistema |
| RS-04 | Mostrar qué servicio interpreta la descripción y qué datos se enviarán antes del primer envío a un servicio externo |
| RS-05 | Por defecto, enviar solo descripción y catálogo mínimo de objetos necesario; no enviar el DWG completo, coordenadas geográficas absolutas o metadatos innecesarios |
| RS-06 | Las credenciales se guardarán en el servidor o en un mecanismo seguro de configuración; nunca en el frontend, URL, GLB ni logs del proyecto |
| RS-07 | Validar la respuesta con un esquema estricto. Rechazar materiales desconocidos, rutas arbitrarias, URLs externas no autorizadas y números no finitos |
| RS-08 | Definir límites configurables de tamaño del prompt, contexto, duración y coste por solicitud. No reintentar automáticamente operaciones facturables sin una política visible |
| RS-09 | Tratar nombres y avisos como texto seguro en el DOM; mantener las protecciones del servidor de conversión |
| RS-10 | Permitir eliminar el texto original de la descripción sin perder el plan de asignaciones ya confirmado |

El proveedor de IA quedará detrás de una interfaz sustituible. No se elige en esta hoja un
modelo, un servicio ni un precio concreto. Antes de producción deberán aprobarse el proveedor,
sus condiciones de tratamiento de datos y los límites de uso. El flujo manual debe permanecer
operativo independientemente de esa decisión.

## 12. Rendimiento, robustez y accesibilidad

Los siguientes son **objetivos de aceptación propuestos**, no prestaciones medidas del producto actual.

| ID | Objetivo y método de verificación |
|---|---|
| RN-01 | Cambiar materiales o ajustar UV no vuelve a ejecutar DWG→DXF ni reconstruye alturas/topología. Verificar mediante fases y contadores de ejecución |
| RN-02 | Interrumpir una petición de IA detiene la espera y descarta respuestas tardías. Si el proveedor no permite cancelar el cálculo remoto, se comunica esa limitación |
| RN-03 | Un error de red, textura o exportación conserva el último modelo y plan confirmados |
| RN-04 | Probar 100, 1.000 y 5.000 edificios, incluyendo instancias y un caso de aproximadamente un millón de triángulos. Registrar hardware, resolución, tiempos, memoria y draw calls |
| RN-05 | Objetivo inicial: previsualizar un cambio de material en 100 objetos en ≤2 s, con texturas ya cargadas, en el equipo de referencia que se fije antes de desarrollar |
| RN-06 | Objetivo inicial: mantener ≥30 FPS en el caso de 1.000 edificios/12 presets a 1920×1080 con sombras medias definidas para la prueba. Publicar también la comparación sin materiales y las limitaciones |
| RN-07 | Reutilizar texturas/materiales idénticos y liberar sus recursos cuando dejen de usarse. Diez ciclos abrir/materializar/cerrar no deben mostrar crecimiento sostenido de recursos vivos |
| RN-08 | Acordar presupuesto de texturas antes de aprobar el catálogo; usar 1K/2K como punto de partida y cargar solo los recursos utilizados. Medir memoria GPU estimada y tamaño exportado |
| RN-09 | Completar carga, revisión de asignaciones y corrección desde teclado y lista alternativa a la selección 3D; avisos accesibles y foco visible |
| RN-10 | En pantallas estrechas, la tabla de asignaciones debe poder usarse sin ocultar confirmar, cancelar ni conflictos pendientes |

Una limitación de rendimiento debe producir un mensaje con una alternativa —reducir resolución
de texturas, dividir el proyecto o conservar materiales simples—, sin dejar el visor bloqueado.

## 13. Escenarios de aceptación de extremo a extremo

| ID | Entrada o acción | Resultado que debe verificarse |
|---|---|---|
| CA-01 | Dos edificios de una capa, ladrillo claro en fachadas y grava en cubiertas | Ambas superficies se diferencian; patios y altura permanecen iguales |
| CA-02 | Tres torres parecidas y «la torre central» sin referencia inequívoca | Se muestran candidatos y queda pendiente la asignación; no se confirma una elección oculta |
| CA-03 | «Todos de hormigón salvo E-004 de ladrillo» | E-004 queda excluido de la asignación general y conserva su excepción |
| CA-04 | «Carpinterías negras» en un DWG de huellas | El sistema informa que faltan esas superficies; no presenta el encargo como completado |
| CA-05 | Diez INSERT iguales; cambiar el material de una copia | Solo cambia la copia seleccionada y la exportación mantiene esa diferencia |
| CA-06 | El mismo edificio en metros, milímetros y UTM, con INSERT escalado | La textura representa la misma longitud real y mantiene su orientación |
| CA-07 | Edificio con patio y zócalo sobre una ladera | Materiales separados; el patio sigue vacío; la cota de planta y el apoyo no cambian |
| CA-08 | Guardar tres cámaras y cambiar el sol | Las asignaciones y UV permanecen; las diferencias visuales corresponden a cámara/luz |
| CA-09 | Corregir un material tras cargar una vista antigua con color de capa | La vista antigua no sustituye silenciosamente la materialidad nueva |
| CA-10 | Exportar GLB y abrirlo sin conexión en otro equipo/Blender | Texturas incorporadas, misma asignación, escala y orientación; sin peticiones de recursos ausentes |
| CA-11 | Exportar GLB con cámaras después de corregir materiales | Las cámaras se añaden a la revisión materializada actual, no al GLB inicial |
| CA-12 | Proveedor devuelve un material/ID inexistente o una URL no autorizada | Respuesta rechazada o marcada pendiente; no se ejecutan operaciones ni descargas arbitrarias |
| CA-13 | Cancelar interpretación o fallar una textura a mitad de la previsualización | El último estado confirmado se conserva y las respuestas tardías no lo alteran |
| CA-14 | Reabrir el mismo proyecto o un GLB diferente con el mismo nombre | Se recuperan las asignaciones compatibles; se detecta y avisa la revisión geométrica distinta |
| CA-15 | Modelo teselado en el MVP | Se muestra la limitación y una alternativa; no se entrega un modelo parcialmente materializado |

### Condición de publicación

- Todos los requisitos P0 y escenarios CA-01…CA-15 aprobados.
- GLB validado sin errores; cualquier advertencia debe explicarse y aceptarse expresamente.
- Pruebas automáticas del plan, UV, prioridades, aislamiento de instancias y persistencia.
- Pruebas de navegador del flujo completo y prueba de importación real en Blender.
- Biblioteca con licencias y escalas revisadas, documentación de limitaciones y benchmark publicado.
- Destino de datos y configuración de IA visibles; flujo manual funcional sin conexión al proveedor.

## 14. Encaje en el repositorio y dependencias

La base actual tiene materiales por capa con color, rugosidad, metalicidad y opacidad. La malla
fusionada por capa y la exportación que parchea el GLB original requieren ampliaciones para
mantener materiales independientes y exportar las correcciones del usuario.

| Área actual | Cambio necesario |
|---|---|
| `pipeline/src/extrude/build.py` y geometría de extrusión/terreno | IDs persistentes, superficies semánticas y separación de zócalo/fachada |
| `pipeline/src/extrude/gltf_export.py` | Primitivas/materiales múltiples, UV, normales/tangentes, imágenes incorporadas y metadatos conservados |
| `pipeline/src/extrude/instancing.py` | Compatibilidad por geometría y asignación material; escala física correcta |
| Configuración y almacenamiento de proyectos | Plan de materialidad versionado separado de las reglas geométricas del YAML |
| Servidor local | Catálogo, interpretación de descripción, validación de plan y materialización; credenciales fuera del navegador |
| `viewer/src/scene/layers.ts`, selección e interfaz | Materiales por objeto/superficie, edición manual, revisión de conflictos y convivencia con capas |
| Vistas y exportaciones del visor | Revisión material global, migración de colores antiguos y bytes exportables actualizados |

No se fija aquí la forma definitiva de endpoints ni una dependencia de software nueva. El
contrato del plan y las pruebas de exportación deben cerrarse antes de elegir la implementación.

## 15. Plan de desarrollo propuesto

| Fase | Entrega | Esfuerzo | Dependencias y salida |
|---|---|---|---|
| 0. Base verificable | Fixtures, catálogo de objetos, IDs y esquema del plan | M | Casos de geometría/apoyo/exportación estables; IDs reproducibles |
| 1. Materialidad manual | Superficies, biblioteca, UV y asignación sin IA | L | Fase 0; GLB materializado validado y selección independiente por objeto |
| 2. Revisión en visor | Panel de asignaciones, previsualización, deshacer y persistencia | M–L | Fase 1; correcciones y reapertura fiables |
| 3. Interpretación del prompt | Adaptador IA, propuesta estructurada y resolución de ambigüedades | M–L | Catálogo/esquema y privacidad aprobados; sin lógica geométrica ejecutada por el modelo |
| 4. Exportación y entrega | GLB con cámaras, PNG, vistas y Blender coherentes | M | Modelo canónico materializado; CA-08…CA-11 aprobados |
| 5. Endurecimiento | Rendimiento, accesibilidad, recuperación y documentación | M | Todos los P0 y criterios de publicación aprobados |

**Orden recomendado:** validar primero que un material asignado manualmente a un edificio llega
correctamente a Blender. Incorporar después el prompt sobre ese mecanismo ya comprobado.

## 16. Decisiones pendientes antes de implementar

1. Proveedor/modelo de interpretación, alternativa local y límites de coste y tratamiento de datos.
2. Origen y licencia de los 12 presets y sus texturas; presupuesto final de tamaño/memoria.
3. Equipo de referencia y aceptación definitiva de objetivos de rendimiento.
4. Diseño final de guardado de proyectos y portabilidad editable P1.
5. Prioridad de la siguiente fase: ventanas/carpinterías generadas, texturas propias o modelos teselados.

Estas decisiones no cambian el objetivo del MVP: **DWG + descripción → asignaciones revisadas →
modelo con materiales reales guardados → varias vistas coherentes y exportación a Blender**.
