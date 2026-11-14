# Historial de cambios

## Sin publicar

_Nada todavía._

## 1.0.1 — 2026-11-14

- Abrir el mismo proyecto en dos ventanas ya no borra vistas: solo guarda la primera; la
  segunda avisa y no guarda, y toma el relevo sola (conservando sus vistas nuevas) cuando la
  primera se cierra o cierra el proyecto. Usa Web Locks del navegador.

## 1.0.0 — 2026-11-14

Primera versión estable: del plano al modelo 3D, su revisión, materiales, vistas e imágenes
de presentación con IA, todo desde el visor web, en local con Docker o en un servidor.

### Instalación en Windows con icono

- `Instalar Extrude.bat`: crea los accesos directos **Extrude** (escritorio y menú Inicio) y
  **Actualizar Extrude**, prepara el archivo de claves de IA y deja Extrude listo.
- El icono arranca Docker Desktop si hace falta, levanta Extrude y lo abre en su propia ventana
  (modo aplicación de Edge o Chrome), siempre en el mismo navegador para conservar vistas e
  imágenes. Guía: `docs/INSTALAR_WINDOWS.md`.
- La imagen de Docker incluye LibreDWG 0.14 (`dwg2dxf`), compilado desde el código fuente
  oficial con su suma verificada: los planos DWG se aceptan directamente, sin convertirlos
  antes a DXF.

### La IA de imágenes distingue viales de edificios

- Leyenda de capas opcional en «Generar con IA» (marcada por defecto): explica al generador
  qué es cada cosa de la captura según el tipo de cada capa visible en la vista (edificios,
  viales, agua, vegetación, terreno). Vale para cualquier proyecto; «Mejorar con Claude» la
  recibe también.
- El importador reconoce más nombres de viales (TRANSPORTE, VIARIO, CARRIL, BICI, AVENIDA,
  AUTOVÍA, AUTOPISTA, RONDA, ROTONDA, ASFALTO, PAVIMENTO, PEATONAL, CAMINO, SENDERO,
  PARKING): antes esas capas se levantaban como edificios y una calle parecía un muro. Las
  palabras ambiguas (EJE) no cuentan. Afecta a las importaciones nuevas.

### Prompts de imagen más completos

- Frases opcionales por categorías en «Generar con IA» (más de 120): rol, estilo, edificios
  completos (ventanas, carpinterías, balcones, plantas bajas, cubiertas), tipos de edificio,
  materiales, gente y vida urbana (personas paseando, corriendo, ciclistas…), movilidad,
  vegetación y paisaje, mobiliario urbano, luz y hora, clima y estación, cámara y ambiente.
  El rol abre el prompt y sustituye al anterior; las frases ya añadidas se marcan.
- «Mejorar con Claude» trabaja como director de visualización arquitectónica: empieza con un
  rol para el generador y pide edificios completos con ventanas en todas las plantas y una
  escala humana realista.

### Correcciones

- «Generar con IA» abierto desde una vista envía solo esa vista; antes se sumaban, sin
  avisar, las elegidas la vez anterior.
- La edición del proyecto con Claude funciona contra la API real: el esquema de respuesta
  superaba los límites de Anthropic y toda propuesta fallaba con HTTP 400.
- Los nombres de las vistas nuevas no se repiten al borrar una anterior.

### Claves de IA en un único .env

- `extrude serve` carga las claves de IA (y las variables `EXTRUDE_*`) del `.env` de la raíz
  del repositorio, el mismo de Docker local. El entorno manda sobre el archivo; muestra solo
  los nombres cargados. `--env-file` y `--no-env-file`; producción usa `--no-env-file` y
  sigue tomando las claves del secreto de Compose.

### Rendimiento y conexiones lentas

- Las teselas de un conjunto se descargan en paralelo (hasta 4) y cada una se analiza al
  llegar, soltando su búfer: la memoria ya no crece con el número de teselas.
- El visor arranca con un 31 % menos de JavaScript (391 → 270 kB gzip): las tablas de la
  luz de área, los cargadores de HDRI y el diálogo de generación con IA se descargan al usarse.
- Una subida por debajo de 16 kB/s (pasados 30 s) se corta con 408: cinco subidas que
  goteaban bloqueaban la cola de conversión de todos. En modo compartido, como máximo dos
  subidas simultáneas por usuario.

### Docker local

- Arranque del visor y la API con `docker compose up -d --build`, accesible en
  `http://localhost:8080`, con volumen de trabajos y claves de IA opcionales.

## 0.1.0 — 2026-11-13

### Correcciones de la auditoría del servidor

- El hilo de conversión ya no muere ante un error al registrar otro error: el trabajo acaba
  en «failed» y los siguientes se convierten; el registro nunca lanza.
- Un segundo `extrude serve` en el mismo puerto falla con un mensaje claro (en Windows el
  puerto es exclusivo: antes arrancaba y se repartían las conexiones).
- `--allowed-host` exige el Host también al escuchar en todas las interfaces (Docker local y
  producción), contra DNS rebinding; Caddy local solo atiende `localhost` y `127.0.0.1`.
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

### Base del proyecto

Pipeline DXF/DWG a GLB, visor Three.js, control solar, capas, vistas guardadas y exportaciones
a PNG, views.json, Blender y GLB con cámaras y luces. El historial Git conserva su desarrollo.
