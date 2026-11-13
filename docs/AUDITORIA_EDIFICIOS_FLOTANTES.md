# Auditoría del problema de edificios flotantes

Fecha: 12 de noviembre de 2026. Repositorio: `C:/Users/balle/Documents/Proyectos/extrude`.

## Dictamen

El fallo observado es real y tiene dos causas encadenadas: el borrador antiguo confunde cotas Z con alturas de edificio y desactiva el apoyo en el TIN; después, la corrección del código y la conversión aislada no llegan al proyecto servido al usuario. No hay evidencia de que el error principal sea de cámara, sombreado, recentrado o caché del GLB.

La modificación de `init_config.py` realizada anteriormente corrige este caso en una ejecución nueva. No migra trabajos existentes, no reinicia procesos Python y no carga el resultado en el visor. La afirmación anterior de resolución fue demasiado amplia: se verificó una salida independiente, no la experiencia del usuario.

Esta auditoría no modifica la implementación, no reinicia servidores y no sustituye proyectos. Se han inspeccionado el DWG convertido a DXF, configuración, informes, GLB, código de pipeline/servidor/visor y pruebas relacionadas. No se ha verificado visualmente la pestaña de Chrome.

## Evidencia del plano y de las salidas

Origen conservado: `C:/Users/balle/AppData/Local/Temp/extrude-jobs/97f7129c15fd46f1b17f1db11921180d/input.dwg`.

| Capa | Polilíneas | Cerradas | Z observadas (m) | Tratamiento automático actual del proyecto antiguo |
|---|---:|---:|---|---|
| `_BLOQUES LINEALES` | 21 | 21 | -61,176370; -45,882278; 0 | building, height_from=elevation, sin apoyo TIN |
| `_TORRES` | 18 | 18 | 0 | ignore |
| `_UNIFAMILIARES` | 577 | 577 | -61,176370; 0 | ignore |
| `contours` | 86 | 2 | -61,176370 a 183,529111 | terrain |

Las unidades leídas son metros, sin evidencia suficiente para atribuir el fallo a escala incorrecta. Que las cotas de edificios coincidan con algunas curvas no prueba su significado de base o coronación.

| Indicador de `_BLOQUES LINEALES` | Original | Archivo corregido independiente |
|---|---:|---:|
| Edificios convertidos | 21 | 21 |
| Altura provisional | 9 m en los 21 | 9 m en los 21 |
| Flotantes | 21 | 0 |
| Enterrados / intersecciones / parcialmente enterrados | 0 / 0 / 0 | 0 / 0 / 0 |
| Huellas cubiertas por el TIN | 21 de 21 | 21 de 21 |
| Edificios con zócalo | 0 | 13 |
| Zócalo máximo | 0 | 9,892517 m |
| Mínimo vertical del GLB, eje Y | 0 m | -61,176369 m |
| Máximo vertical de esta capa en el GLB | 9 m | -36,882278 m |

En la geometría previa a exportación, el máximo error absoluto de contacto muestreado del zócalo es aproximadamente `5e-14 m`. Esto comprueba los puntos evaluados por el diagnóstico, no constituye una validación estructural ni un certificado de toda la envolvente.

Revisiones geométricas:

- Original: `sha256:3866bbc7e4cbf61d3a8a1000bb0ac8310ae967ea0fdaa33d2f712395bb8d6e99`.
- Corregida: `sha256:5ea54130c9fbbe45e37511ffda1380b3f0f6b60203e8a28dba0ff49729429bfa`.

Salida corregida: `out/apoyo_bloques_lineales/model.glb`. Evidencias numéricas de esta auditoría: `out/auditoria_edificios/evidencias.json`.

## Hallazgos confirmados

### 1. P1: cotas absolutas usadas como alturas y base fija en cero

El YAML antiguo declara `height_from: elevation` sin `base_from_terrain`. En `heights.py`, la altura se calcula como `footprint.elevation - rule.base_elevation`. Para estas cotas no positivas se rechaza el resultado y se toma `default_height: 9`. En `build.py`, la base sigue siendo 0 al no estar activados los modos de apoyo. Resultado: prismas entre Z=0 y Z=9 sobre terreno negativo.

Referencias: `pipeline/src/extrude/init_config.py`, `_building_rule` y `draft_config`; `pipeline/src/extrude/heights.py`, `_from_source` y `resolve_height`; `pipeline/src/extrude/build.py`, `_extrude_layer`.

La corrección previa evita inferir altura desde Z cuando se ha detectado terreno y propone altura provisional con apoyo TIN. Es apropiada para este caso, pero no demuestra la altura real. No debe reinterpretar silenciosamente las reglas explícitas del usuario.

### 2. P1: el servidor continúa sirviendo el modelo antiguo

Los trabajos `97f7129c15fd46f1b17f1db11921180d`, `8a9f7b6cf3884cb488e51b5928377a9b` y `7b12730393ce4e18967ef41ff5c13659` conservan el YAML antiguo y exactamente la misma revisión geométrica con 21 flotantes. Los dos últimos son posteriores al cambio de `init_config.py`.

Se consultó `http://127.0.0.1:8765/api/health`: servidor disponible, versión `0.1.0`. Para el trabajo `7b12730393ce4e18967ef41ff5c13659`, el servidor entrega el YAML antiguo y el GLB de 1.018.276 bytes. Ambos incluyen `Cache-Control: no-store`.

Se observaron tres grupos de procesos `extrude serve`, iniciados a las 19:54:11, 23:09:34 y 23:10:13; el archivo corregido tiene modificación a las 23:31:23. Los procesos pertenecen al entorno de este repositorio. No se determinó cuál escucha en cada puerto. La evidencia es consistente con código antiguo cargado en memoria; no es prueba de caché del navegador. La versión `0.1.0` no permite distinguir las compilaciones.

### 3. P1: los proyectos antiguos no se reparan al cambiar alturas o reabrir

`server.py::_reconvert` lee el `config.yaml` existente y llama a `apply_heights`. `layer_heights.py::_override` sustituye las fuentes de altura, pero conserva los campos de apoyo originales. Cambiar una altura a 12 m puede dejar la base en 0. Reabrir desde `main.ts::reopenRecent` recupera el resultado guardado; no regenera el borrador.

Hace falta una operación explícita para revisar/aplicar el apoyo en proyectos existentes. Reiniciar el servidor permite usar el código nuevo, pero no migra por sí solo un YAML ya guardado.

### 4. P1: edificios potenciales ausentes del modelo

`_guess_kind` no reconoce `_TORRES` ni `_UNIFAMILIARES`; el informe las marca `ignored`. Quedan sin convertir 18 y 577 polilíneas cerradas respectivamente. Son candidatos por nombre y geometría; sus alturas y el número final de edificios tras topología requieren revisión. No debe afirmarse que hay exactamente 595 edificios adicionales sin verificar huellas y duplicados.

La interfaz muestra contadores de los sólidos convertidos. Por eso 0 flotantes puede coexistir con capas de edificios enteras excluidas. Los equipamientos también presentan nombres no reconocidos. Hace falta ofrecer clasificación revisable y un resumen visible de capas ignoradas.

### 5. P2: zócalos altos resuelven contacto, no la implantación del proyecto

`terrain_support: foundation` coloca la planta a la cota máxima bajo la huella y extiende paredes hasta el terreno. Mantiene la altura del edificio desde la planta y evita deformar la cubierta. En este plano añade hasta 9,89 m de zócalo: requiere revisar acceso, plataformas, topografía y altura real. No prueba que la solución arquitectónica sea correcta.

No se debe sustituir esto por trasladar toda una capa una distancia global: cada huella ocupa terreno distinto.

### 6. P2: falta una reparación accesible y verificable en el visor

`QualitySummary` ofrece contadores, avisos y enlaces al YAML/informe; no permite corregir el apoyo desde allí. `applyHeights` sí espera la conversión y llama a `showConverted` para sustituir el modelo. Esa infraestructura puede reutilizarse para apoyo, pero actualmente el usuario recibe un consejo técnico sin una acción equivalente.

El resultado de conversión incluye revisión en el informe y el GLB contiene `geometry_revision`, pero `showConverted` no comprueba que sean iguales antes de presentar el resultado. Hace falta asociar modelo cargado, informe y configuración a la misma revisión y mostrar si la carga de una revisión nueva falla.

### 7. P2: diagnóstico con tolerancia y alcance limitados

`GROUND_TOLERANCE_M=1.0`: 0 flotantes significa que no se supera ese criterio, no contacto milimétrico. Para prismas ordinarios se detecta flotación completa comparando extremos. Para zócalos se usa el mínimo del hueco de contacto para clasificar flotación; el máximo se informa pero no dispara una categoría propia. Un contacto parcial no es equivalente a apoyo completo.

El pipeline dispara el aviso agregado de capas sin apoyo al 10 %, mientras `groundCheck.ts` usa el 1 %. Los contadores no se falsifican, pero los avisos pueden diferir. Deben distinguirse flotación completa, huecos parciales, intersección, ausencia de cobertura y contacto no verificado.

## Riesgos encontrados en código, no acreditados como causa de esta captura

- El TIN de apoyo se construye conjuntamente con todas las capas terrain; la geometría visible se triangula por capa. Con varias capas, ambas superficies pueden diferir. En este caso hay una sola capa de curvas: no se ha demostrado esa divergencia.
- `_terrain_samples` incorpora polilíneas y puntos, no MeshFeature. Una topografía formada por 3DFACE/MESH puede verse sin alimentar el apoyo. El generador tampoco obtiene `has_elevation` de sus vértices. No es el formato de `contours` en este plano.
- La detección de terreno se basa en nombre y presencia de Z/puntos; no garantiza que se pueda construir un TIN no degenerado. Debe validarse antes de dar el apoyo por disponible.
- `meshes3d.tin` elimina duplicados XY por tolerancia conservando una muestra; no diagnostica Z incompatibles en un mismo XY. Falta tratar la ambigüedad topográfica explícitamente.
- Delaunay no impone las curvas como líneas de rotura. Que una huella esté dentro de la envolvente convexa no demuestra precisión topográfica local. No se propone cambiar el algoritmo sin una reproducción específica.
- El constructor de mallas de origen preserva sus coordenadas y no pasa por la misma extrusión. Debe explicarse la capacidad de reparación antes de ofrecerla para cualquier tipo de objeto.

## Verificaciones realizadas y límites

- Lectura real del DWG mediante el conversor disponible y análisis de sus polilíneas.
- Comparación del YAML, informes, revisiones, contadores y posiciones exportadas en ambos GLB.
- Consulta de solo lectura al servidor local y fechas de procesos.
- 100 pruebas Python pasaron en la primera ejecución ampliada; 4 pruebas HTTP quedaron bloqueadas por permisos de sockets del entorno. Tras repetir el archivo de reconversión con acceso local autorizado, pasaron sus 18 pruebas. Los 104 casos distintos del conjunto seleccionado quedaron cubiertos satisfactoriamente.
- 28 pruebas del visor pasaron: diagnóstico, resumen de informe y alturas por proyecto.
- No se ejecutó toda la suite del repositorio ni se realizó una comprobación visual de Chrome en esta auditoría. Las pruebas existentes no cubren toda la reparación de apoyo y recarga propuesta.
- Hay cambios previos del usuario en servidor y visor; deben preservarse. No se han revertido ni mezclado aquí con nuevas correcciones.

## Orden de solución recomendado

1. Identificar el proceso/puerto del servidor del visor, reiniciarlo de manera controlada y verificar una identidad de compilación inequívoca.
2. Añadir reparación explícita de apoyo por capa para proyectos existentes, preservando las reglas y las alturas fiables; guardar procedencia del cambio.
3. Conectar esa reparación a reconversión y recarga del mismo proyecto, con informe/GLB/revisión coherentes, recuperación ante fallo y tratamiento explícito de materiales/ediciones.
4. Mostrar y permitir revisar las capas ignoradas; proponer `_TORRES` y `_UNIFAMILIARES` como candidatos sin inventar alturas definitivas.
5. Validar contacto geométrico y alturas reales, y revisar zócalos excesivos. Añadir pruebas de regresión y comprobación visual antes de declarar resuelto el problema del usuario.

No basta con copiar `base_from_terrain: true` en todos los objetos: hay bases explícitas que deben respetarse, geometrías de origen que requieren otro tratamiento y terrenos cuya cobertura o interpretación necesitan revisión.
