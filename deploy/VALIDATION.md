# Evidencia y límites de validación

Fecha: 13-11-2026. Windows, Node local 22.13.1, Python local 3.13.2, uv 0.13.7.
Las imágenes especifican Python 3.12.13 y Node 22.22.0: no se ha ejecutado esa
combinación Linux aquí. No hay Docker en este entorno.

## Comprobado

- `npm run build` en viewer: éxito. Incluye index, guía, fuentes WOFF2,
  Draco WASM/JS y modelos de ejemplo. Avisa de chunk principal >1200 kB.
- 31 pruebas seleccionadas del visor: cliente, destino, sesiones y selección
  del origen en desarrollo/build: éxito.
- Suite completa del visor: 291/292 pasan en sandbox. El caso restante de Blender
  headless no encontró su informe en el directorio temporal virtualizado; el
  archivo de exportación completo (14 tests, incluido ese caso) pasa fuera del
  sandbox. No quedan fallos de test sin resolver; se usaron dos ejecuciones.
- 32 pruebas HTTP: `test_http_server`, `test_shared`, `test_server_origin`,
  `test_server_capacity`: éxito. Se ejecutaron fuera del sandbox porque este
  bloquea sockets loopback (WinError 10013), sin contactar servicios externos.
- 93 pruebas adicionales de rutas API, conversión asíncrona, materiales,
  IA e imágenes con proveedores falsos locales: éxito (`test_server`,
  `test_server_async`, `test_server_materials`, `test_server_ai`, `test_image_gen`).
- 8 pruebas `test_master`: éxito; compresión Draco/Meshopt, edición del maestro
  y reexportación mediante herramientas Node locales.
- 5 pruebas `test_deploy`: éxito; copia/restauración de cuentas, ACL y revisiones,
  rechazo de sobrescritura y rutas tar ajenas, claves de proveedor permitidas.
- Ruff lint y formato de archivos nuevos Python y scripts deploy: éxito.
  Mypy estricto del servidor HTTP acotado: éxito. Prettier de los dos archivos
  TypeScript tocados: éxito. `git diff --check`: sin errores.
- Parseo YAML y comprobación estructural de Compose: éxito; API sin puertos
  publicados. Esto no sustituye `docker compose config`.

## Pendiente antes de un despliegue público

- Construcción real de ambas imágenes, resolución de tags/bases, dependencias
  dinámicas y permisos de los volúmenes en Linux.
- `docker compose config`, `caddy validate`, healthcheck y arranque como UID 10001.
  El workflow `deploy-check.yml` ejecuta esas comprobaciones cuando se dispare;
  no se ha publicado ni ejecutado desde esta tarea.
- HTTPS/ACME, redirección HTTP, 413, 503, tiempos de subida/descarga y firewall
  desde un cliente externo. El smoke HTTPS mantiene validación de certificado.
- Prueba visual en navegador de guía, fuentes, modelos y decodificación GLB.
  El build local puede contener modelos personales ignorados de desarrollo;
  la imagen los excluye mediante .dockerignore y vuelve a copiar solo tres ejemplos.
- Ensayo de backup/restore sobre volúmenes Docker reales y release conservada.
- Validación con planos representativos, carga sostenida, crecimiento de revisiones,
  disco lleno, OOM y cortes durante escritura. No hay garantía transaccional global.
- DWG: la imagen incluye LibreDWG 0.14 (`dwg2dxf`), compilado desde el código fuente oficial
  con suma verificada. Validado con un DWG de muestra; los DWG muy recientes pueden fallar y
  entonces se recomienda DXF 2018. No implica aprobación general para producción.
