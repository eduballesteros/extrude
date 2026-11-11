# Historial de cambios

## Sin publicar

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
