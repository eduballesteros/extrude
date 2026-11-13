# Historial de cambios

## Sin publicar

### Correcciones de la auditoría del servidor

- El hilo de conversión ya no muere ante un error al registrar otro error: el trabajo acaba
  en «failed» y los siguientes se convierten; el registro nunca lanza.
- Un segundo `extrude serve` en el mismo puerto falla con un mensaje claro (en Windows el
  puerto es exclusivo: antes arrancaba y se repartían las conexiones).
- `--allowed-host` exige el Host también al escuchar en todas las interfaces (producción lo
  pasa con el dominio publicado), contra DNS rebinding.
- Límites de acceso por cliente y usuario y por cliente en total (`--trust-forwarded` detrás
  de Caddy); los fallos de un tercero ya no bloquean al dueño de la cuenta; claves acotadas.
- Los estados de trabajos terminados no guardan el informe en memoria (se lee de disco).
- El contacto de los objetos que siguen la pendiente se mide en la malla.
- Solo se aceptan los EPSG UTM que existen; el visor rechaza un YAML demasiado grande con un
  mensaje claro y no propone comandos si el servidor es web.

### Apoyo sobre el terreno y revisión del proyecto

- Apoyo automático por objeto (`base_auto`) en las importaciones nuevas, decidido con el
  terreno realmente triangulable; una cota Z ya no se toma como altura si hay terreno.
- `height_from: elevation` es la cota absoluta de cubierta: altura = cubierta − base real.
- Procedencia de altura y base por objeto (acreditada, inferida, provisional) en GLB e informe.
- Validez del terreno (cotas nulas, puntos alineados, duplicados incompatibles, mallas 3D).
- Contacto: nuevas categorías «contacto parcial» y «sin verificar»; aviso con un solo sólido.
- Capas ignoradas con geometría como pendientes de revisión; resumen de calidad con lo
  convertido, descartado, ignorado y pendiente; zócalos de más de 3 m señalados.
- Visor: «Revisión del proyecto» (reparar apoyo, clasificar capas, recuperar el resultado
  anterior), proyectos del servidor en la pantalla de inicio, comprobación de revisión tras
  cargar («generado pero no cargado») y aviso de servidor desfasado.
- `GET /api/health` identifica el código en ejecución. Ver
  [docs/APOYO_Y_COTAS.md](docs/APOYO_Y_COTAS.md).

### Ajuste del modelo e imágenes desde el visor

- Altura por capa en el panel Capas: valor efectivo, pendiente y guardado, validación junto al
  campo, YAML actualizado y reconversión del plano con sustitución solo si termina bien.
- Selección de capa con resaltado de todos sus elementos (mallas, instancias, líneas, teselas).
- Lupa al medir con punto de mira y ajuste a vértice, borde o superficie.
- Capturas HD y 4K de vistas guardadas, renderizadas al tamaño real con la proporción de la vista.
- Biblioteca única de vistas e imágenes (panel Vistas y menú principal) guardada en IndexedDB:
  tamaño completo, comparación con el original, descarga y borrado.
- Generación de imágenes con IA a partir de vistas y un prompt libre (OpenAI, Gemini, proveedor
  simulado), con revisión previa del envío, resolución real guardada y ampliación etiquetada.
- Sugerencia de prompt con Claude a partir de las vistas, editable y opcional.
- «Reabrir el último proyecto» desde la pantalla de inicio.
- Ver [docs/VISTAS_E_IMAGENES.md](docs/VISTAS_E_IMAGENES.md).

### Conversión y terreno

- Reconstrucción del TIN y apoyo de edificios con planta horizontal y zócalo adaptado al terreno.
- Superficies de altura cero sin zócalo y diagnóstico seguro cuando faltan puntos de contacto.
- Separación de cotas al reconstruir y deduplicar huellas que usan Z como base o altura.
- Informes de cobertura para todos los objetos comprobados y avisos de extrapolación fuera del TIN.
- Resolución de alturas, lectura de bloques, geometría y límites de exportación más robustos.
- Geolocalización desde CRS UTM para situar el estudio solar.

### Servidor y carga

- Trabajos asíncronos con progreso por fases, capacidad acotada y retención de resultados.
- Respuesta 503 inmediata, sin leer el cuerpo, cuando la cola está llena.
- Validación de origen, host, parámetros y YAML; los errores de entrada incluyen diagnóstico.
- Descarga de teselas con índices negativos y sin el límite artificial de seis dígitos.
- Cancelación de espera, recuperación de trabajos y reapertura de resultados sin reenviar el plano.

### Visor

- Interfaz revisada, temas claro y oscuro, medición y resumen persistente de calidad.
- Destino efectivo visible y aceptación explícita de servidores remotos introducidos por URL.
- Recursos glTF relativos al modelo o tesela, incluida su URL final tras redirecciones.
- Validación de vistas importadas, campos de cámara, fechas, luces y dimensiones.
- Conservación y serialización de nombres de capa especiales como `__proto__`.
- Gestión de recursos y cargas de vistas más resistente a resultados que llegan tarde.

### Repositorio

- README con portada, instalación, configuración, uso, API, limitaciones y documentación de marca.
- Favicons SVG/ICO, iconos Apple y manifest generados desde una única geometría.
- Ejemplos disponibles automáticamente al ejecutar el visor desde un clon limpio.
- Integración continua, guía de contribución y plantilla de revisión.
- `just inspect` protege rutas con espacios y apóstrofos.

## 0.1.0 — Base del proyecto

Pipeline DXF/DWG a GLB, visor Three.js, control solar, capas, vistas guardadas y exportaciones
a PNG, views.json, Blender y GLB con cámaras y luces. El historial Git conserva su desarrollo.
