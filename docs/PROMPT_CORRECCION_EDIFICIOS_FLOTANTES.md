# Prompt para corregir el problema de extremo a extremo

Trabaja en `C:/Users/balle/Documents/Proyectos/extrude`. Corrige el problema recurrente de edificios flotantes hasta que el proyecto que abre el usuario use realmente la geometría corregida. Lee primero `docs/AUDITORIA_EDIFICIOS_FLOTANTES.md`. No te limites a producir otro GLB en una carpeta independiente.

Contexto comprobado:

- El DWG original está en `C:/Users/balle/AppData/Local/Temp/extrude-jobs/97f7129c15fd46f1b17f1db11921180d/input.dwg`. Si ha caducado, localiza el mismo plano en los trabajos conservados antes de pedirlo al usuario.
- `_BLOQUES LINEALES` contiene 21 polilíneas cerradas con Z en -61,176370, -45,882278 y 0 m. El YAML antiguo usa `height_from: elevation` y no activa apoyo en terreno. Esas Z no positivas provocan altura por defecto de 9 m; la extrusión queda desde Z=0 y los 21 edificios flotan.
- Hay 86 polilíneas en `contours`, con cotas entre -61,176370 y 183,529111 m. Las 21 huellas tienen cobertura completa del TIN.
- Existe una corrección previa sin confirmar en Git en `pipeline/src/extrude/init_config.py`: con terreno, no inferir altura de cubierta únicamente desde Z; usar altura provisional y apoyo TIN. Revísala y conserva el comportamiento explícito del usuario.
- `out/apoyo_bloques_lineales/model.glb` es una salida independiente con 0 flotantes, 13 zócalos y zócalo máximo de 9,892517 m. No se ha acreditado que el usuario la vea. Sus 21 alturas de 9 m son provisionales.
- Los trabajos recientes `8a9f7b6cf3884cb488e51b5928377a9b` y `7b12730393ce4e18967ef41ff5c13659` siguen teniendo el YAML y la revisión antiguos. Durante la auditoría, `127.0.0.1:8765` servía ese GLB antiguo con `Cache-Control: no-store`. Había procesos iniciados antes del cambio; comprueba nuevamente el estado antes de actuar.
- `_TORRES` (18 polilíneas cerradas) y `_UNIFAMILIARES` (577) están ignoradas por el clasificador. Son candidatos a edificios pendientes de clasificación y altura; no presupongas el número final de objetos.

Realiza el trabajo, no solo un plan:

1. Inspecciona las instrucciones aplicables y los cambios existentes. Preserva el trabajo del usuario. Identifica el servidor y el puerto que usa realmente el visor. Comprueba qué versión está ejecutándose; reinicia únicamente el proceso pertinente cuando sea necesario y verifica que la versión corregida esté activa. No detengas todos los procesos Python. Añade identidad de compilación/inicio suficiente para detectar este desfase en el futuro sin depender de `version: 0.1.0`.

2. Separa conceptualmente cota absoluta de base, cota absoluta de cubierta y altura relativa. No infieras altura solo porque alguna entidad tenga Z distinta de cero. Conserva alturas fiables de textos, atributos, bloques y reglas explícitas. Si falta altura, muestra la suposición y permite revisarla. Para cotas de cubierta explícitas, define y prueba su relación con el apoyo TIN: no sumes una cota absoluta al terreno como si fuera altura relativa.

3. Permite reparar el apoyo de capas en proyectos ya convertidos mediante una operación explícita del servidor y una acción comprensible del visor, por ejemplo «Revisar apoyo en terreno». Muestra las capas afectadas, el modo propuesto y las advertencias de zócalos. No cambies automáticamente bases explícitas verificadas ni reglas de otras capas. Respeta la prioridad de patrones y conserva comentarios/configuración cuando sea posible. Reabrir o cambiar alturas no debe presentarse como reparación si sigue usando el apoyo antiguo.

4. Reutiliza la reconversión del proyecto con resultados temporales y recuperación ante fallo. Integra la operación con `showConverted`: carga el GLB nuevo, actualiza calidad, descargas y configuración, y conserva cámara/sol/estado compatible. Verifica que la revisión de la geometría cargada coincida con la del informe correspondiente. Si el servidor confirma y falla la carga, comunica «generado pero no cargado» y ofrece reintento. Conserva la salida anterior ante fallos y evita afirmar éxito antes de cargar la revisión nueva. Trata explícitamente materiales y ediciones existentes: preservación o aviso con opción de copia recuperable antes de sustituirlas.

5. Revisa el clasificador y la visibilidad de capas ignoradas. Ofrece `_TORRES` y `_UNIFAMILIARES` como candidatas revisables a building y audita los equipamientos. No conviertas cada capa desconocida en edificio ni inventes alturas definitivas. Informa de entidades/huellas excluidas para que «0 flotantes» no sugiera que todos los edificios del plano están modelados.

6. Usa apoyo por huella con cubierta horizontal y zócalo hasta el TIN cuando sea la solución elegida. No traslades toda una capa una distancia global ni deformes edificios siguiendo la pendiente. Advierte de zócalos excesivos; resolver contacto no valida cotas de acceso ni implantación arquitectónica. Revisa que terreno visible y superficie de apoyo sean coherentes; añade reproducciones para varias capas y terreno de malla si quedan dentro del alcance de la corrección. No cambies la triangulación topográfica sin evidencia y pruebas.

7. Fortalece el diagnóstico sin ocultar errores ni aumentar tolerancias para aprobar el modelo. Distingue flotación completa, huecos parciales, intersecciones, cobertura insuficiente y contacto no verificado. Documenta la tolerancia actual de 1 m y usa verificaciones más precisas para el contacto generado. Comprueba la malla exportada, no solo etiquetas del modo de apoyo. Alinea criterios de avisos del pipeline y visor o documenta su diferencia.

8. Añade pruebas de regresión pertinentes: cotas negativas/cero/mixtas, terreno en cotas positivas y negativas, base explícita preservada, reparación de YAML antiguo, cambio de altura que conserva el apoyo elegido, volver a abrir, fallo durante reconversión/carga, revisión coherente, instancias, patios y huellas fuera del TIN. No copies el DWG privado a fixtures versionadas; usa fixtures sintéticas y el original solo para validación local. Ejecuta las pruebas existentes relevantes.

9. Verifica el resultado en el proyecto servido al usuario. Identifica el trabajo y la revisión antes/después; comprueba el GLB descargado, el informe y el modelo que recibe el visor. Si hay control de navegador disponible, verifica además la escena y el panel de calidad. Si el control visual está bloqueado, declara ese límite y entrega un procedimiento de comprobación exacto; no afirmes haberlo visto. No necesitas autorización adicional para lecturas, pruebas o correcciones reversibles dentro del alcance solicitado; respeta las aprobaciones exigidas por las herramientas.

Criterios de aceptación:

- Una ejecución nueva con la configuración automática corregida ya no repite la regla errónea del plano.
- Un proyecto antiguo puede reparar su apoyo sin requerir editar YAML a mano ni volver a importar a ciegas.
- Los 21 bloques de la reproducción quedan comprobados contra el terreno con 0 flotantes/enterrados, sin esconder capas ni falsear contadores. Si un caso no puede repararse, queda identificado y explicado.
- Las alturas provisionales, capas ignoradas y zócalos altos siguen visibles como pendientes de revisión.
- GLB, informe, configuración y revisión cargada corresponden al mismo resultado; al reabrir no reaparece silenciosamente una revisión anterior.
- Las pruebas relevantes pasan y se informa de los límites de la validación visual/topográfica.

Entrega un resumen de causas verificadas, archivos modificados, pruebas ejecutadas, trabajo/revisión realmente actualizado y pendientes. No declares resuelto el problema solo porque exista un archivo corregido fuera del proyecto activo.
