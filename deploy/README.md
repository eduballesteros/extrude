# Extrude en un servidor Linux

Hipótesis: un servidor, una instancia de API y un proxy Caddy con TLS automático.
El visor y `/api/` comparten origen. No se usa Vite como servidor de producción.
Este trabajo prepara artefactos; no acredita un despliegue público ni modifica
infraestructura. El prompt recibido acaba en «limpieza»; se cubre ese punto con
retención, vigilancia y procedimientos conservadores de mantenimiento.

## Decisiones y bloqueos

| Área | Para el primer despliegue | Trabajo posterior |
| --- | --- | --- |
| API HTTP | 16 conexiones/hilos como máximo, timeout de socket 30 s, proceso único tras proxy | Servidor HTTP de producción, límites globales por IP y pruebas de carga sostenida |
| Conversión | 1 conversión activa + 4 admitidas; 503 y Retry-After al saturar | Cola durable, cancelación de procesos y aislamiento por trabajo |
| Archivos no confiables | Usuarios conocidos, contenedor sin privilegios y recursos limitados | Sandbox por conversión antes de aceptar registros públicos o usuarios hostiles |
| DWG | **Bloqueado en la imagen base**: no lleva ODA ni LibreDWG. DXF funciona sin conversor | Imagen derivada con conversor Linux y licencias/dependencias verificadas |
| Persistencia | Proyectos, permisos, revisiones y cuentas en volúmenes; copia con servicios detenidos | Cuotas por bytes, borrado desde administración y monitorización central |
| Reinicios | Sesiones, cola, estados de sondeo e imágenes en curso no sobreviven | Recuperación durable y reconciliación de operaciones interrumpidas |
| Evidencia | Build y pruebas locales según `VALIDATION.md` | Construir/probar Docker, TLS, restauración y GLB comprimido en Linux antes de abrir tráfico |

No usar varias réplicas, gunicorn workers ni dos procesos sobre los volúmenes:
sesiones, límites, cola y bloqueos de proyecto son de memoria. Un proxy no resuelve
esto. El límite HTTP evita crecimiento ilimitado de hilos; no constituye defensa
completa contra abuso distribuido. El login limita fallos por nombre, no por IP;
un atacante puede alternar nombres y ocupar las 16 conexiones. Para un servicio
abierto a desconocidos faltan protección de acceso en red y validación bajo carga.
Mantener usuarios invitados y restringir acceso en el firewall hasta validarlo.

## Preparar un servidor limpio

1. Linux x86_64 con al menos 4 vCPU, 8 GiB RAM y 40 GiB libres como punto inicial.
   Medir con planos reales: 100 MiB de DXF puede expandirse mucho en memoria/disco.
   Instalar Git, Python 3 para el cliente smoke y Docker Engine con Compose v2
   siguiendo [Docker para Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
   o la guía oficial de la distribución. No ejecutar instaladores remotos sin revisión.
2. Checkout de una release que incluya el estado funcional previo del proyecto y
   estos cambios. El stash de esta tarea depende de ese estado previo: no es una
   release autónoma sobre HEAD. Registrar commit y asegurar un árbol limpio antes
   de construir una release. No copiar `.venv`, `node_modules`, `out` ni cuentas locales.
3. El operador debe configurar A/AAAA del dominio y permitir TCP 80/443 al servidor
   (además de su acceso administrativo restringido). Eliminar AAAA incorrectos.
   No abrir 8765. Salida a DNS/ACME y HTTPS a proveedores de IA si se habilitan.
   Docker puede gestionar reglas propias: comprobar exposición desde otra máquina.
4. Desde la raíz del checkout:

   ```sh
   cp deploy/.env.example deploy/.env
   # Editar dominio real, correo y etiqueta única de release.
   umask 077
   mkdir -p deploy/private
   cp deploy/providers.example.json deploy/private/providers.json
   chmod 700 deploy/private
   # El proceso UID 10001 debe poder leer el secreto montado por Compose.
   sudo chown 10001:10001 deploy/private/providers.json
   sudo chmod 400 deploy/private/providers.json
   sh deploy/ops.sh build
   sh deploy/ops.sh check
   sh deploy/ops.sh user operador
   sh deploy/ops.sh user verificador
   sh deploy/ops.sh up
   sh deploy/ops.sh status
   ```

El alta pide una contraseña dos veces, sin mostrarla. Sin usuarios la API rechaza
el arranque. `users add` con un nombre existente cambia su contraseña. No hay
contraseña predeterminada. Los volúmenes nuevos reciben directorios propiedad de
10001 desde las imágenes; con volúmenes importados conservar/corregir ese UID/GID.
No usar `chmod 777` ni montar el socket Docker en los contenedores.

`ops.sh check` valida Compose y Caddy sin arrancar el servicio público. `build`
descarga imágenes y dependencias, pero no publica nada. El proxy escucha dentro
del contenedor en 8080/8443 sin capabilities; Docker publica solo 80/443.
Caddy obtiene el certificado y redirige HTTP a HTTPS automáticamente. Los
certificados viven en `certificates`; no publicar el puerto de administración
(deshabilitado). No habilitar HSTS con subdominios o preload sin una decisión aparte.

## Configuración y secretos

`.env` contiene únicamente dominio, correo y etiqueta, nunca claves. Las tres
claves admitidas por el wrapper son `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` y
`GEMINI_API_KEY`. El JSON inicial `{}` deshabilita proveedores que necesitan claves.
Añadir solo los proveedores necesarios mediante un editor seguro en el servidor:
los valores se montan en `/run/secrets/providers`, el wrapper los introduce en el
entorno del proceso y hace exec del CLI. No es un almacén cifrado: proteger disco,
host y acceso Docker, que equivale a privilegios administrativos.

No pasar claves como build args, `VITE_*`, argumentos de CLI o comandos de shell
guardados en el historial. El contexto Docker es una lista permitida y excluye
`deploy/private`, `.env`, salidas y datos. No guardar archivos sensibles bajo
`viewer/src` o `viewer/public`, que son código y recursos publicados.
No se habilitan access logs del proxy. Los logs operativos rotan a 3 × 10 MiB por
servicio; no volcar cuerpos, cabeceras Authorization ni secretos durante diagnóstico.

Para rotar: editar el JSON, verificar dueño/permisos, revocar la clave anterior en
el proveedor y recrear API con `docker compose --env-file deploy/.env -f
deploy/compose.yaml up -d --no-deps --force-recreate api`. Se pierden las sesiones
y tareas en curso: drenar primero. Los modelos/opciones existentes se leen en
`ai_providers.py`, `ai_planner.py` e `image_gen.py`; esta plantilla no los inventa
ni los expone al navegador. Para habilitar una variable de modelo existente,
añadirla expresamente a `api.environment` y validar su disponibilidad/coste.
Configurar límites de gasto en cada proveedor por separado.

El wrapper usa flags reales de `extrude serve`: `--host 0.0.0.0 --port 8765
--shared --users /data/accounts/users.json --jobs-dir /data/jobs --origin
https://DOMINIO --max-mb 100`. Verificar con `docker compose --env-file deploy/.env
-f deploy/compose.yaml run --rm --no-deps api serve --help`.

## Red, navegador y archivos

El build del visor toma `location.origin` por defecto. En desarrollo conserva
`http://127.0.0.1:8765`; `VITE_EXTRUDE_SERVER` y `?server=` mantienen los mecanismos
existentes de selección y consentimiento. No construir producción con una variable
VITE heredada que apunte a loopback. El Dockerfile no recibe archivos `.env`.

El backend permite CORS solo para el origen HTTPS configurado; POST del navegador
valida Origin y Content-Type. Scripts sin Origin siguen necesitando Bearer en modo
compartido. Las sesiones son tokens de 256 bits, 8 horas, en sessionStorage del
navegador; logout/reinicio los invalida. Cambiar contraseña no revoca tokens activos:
reiniciar la API si se requiere revocación inmediata de todas las sesiones.

No hay raíz estática del backend. Caddy publica solo `/srv`, copiado desde `dist`;
`/api` y `/api/*` van a la API sin quitar prefijos, sin fallback HTML. El resto usa
archivos reales y 404. Los originales, cuentas, ACL y copias no están en `/srv`.
La API sirve archivos de proyecto con lista permitida y permisos por usuario
(404 si no tiene acceso); imágenes IA comprueban su propietario. Proyectos locales
sin `access.json` no se asignan ni se muestran automáticamente en modo compartido.

Proxy y CLI limitan plano/GLB a 100 MiB (104857600 bytes); imágenes tienen además
su límite específico de 72 MiB y JSON de materiales/IA límites inferiores.
Cabeceras HTTP tienen su límite propio: un YAML grande en X-Extrude-Config puede
fallar aunque el plano quepa; probar configuraciones reales. Proxy: cabeceras 10 s,
cuerpo 120 s; socket backend 30 s por operación. Respuesta de una operación larga:
900 s para cabeceras. El visor usa conversión asíncrona, sondeo de 400 ms, espera
total de 15 min y petición de 60 s por defecto: no es una garantía de duración de
la conversión. Una red lenta puede superar esos límites y requerir retomar sondeo.

## Datos, recursos y reinicios

| Ubicación | Contenido | Copia |
| --- | --- | --- |
| volumen `projects` → `/data/jobs` | originales, YAML, resultados, ACL, revisiones, undo/redo, `_images` y, por proyecto reconvertido, el resultado anterior (`out.prev`, `config.prev.yaml`: hasta el doble de disco) | Incluido por ops.sh backup |
| volumen `accounts` → `/data/accounts` | usuarios con hash scrypt y sal | Incluido; proteger como dato sensible |
| `certificates`, `caddy_config` | certificados ACME y estado de Caddy | Copia de host/volumen aparte o reemisión controlada |
| `deploy/private/providers.json` | claves de proveedor | Copia cifrada separada; nunca Git |
| `/tmp` | temporales de procesos | No se copia, tmpfs de 512 MiB |
| navegador IndexedDB | vistas, miniaturas, biblioteca de capturas e imágenes IA guardadas como Blob | Exportar views.json y descargar imágenes desde el visor |
| navegador localStorage/sessionStorage | ajustes, proyecto reciente, alturas/consentimientos locales, sesión | No incluido por servidor |

API: 2 CPU, 4 GiB, 128 procesos/hilos; proxy: 1 CPU, 256 MiB, 64 procesos/hilos.
BLAS/OMP usan un hilo. 16 peticiones pueden coexistir con tareas de fondo, una
conversión y dos generaciones de imágenes. Hay rutas de edición y consultas IA
síncronas que no pasan por la cola de conversiones: no prometer serialización
global. Vigilar RSS/OOM y latencia; un OOM mata todo el proceso y sus trabajos.

Los proyectos con propietario son persistentes y tienen cuota de 50 por usuario,
no por bytes. En modo local solo se conservan 10 trabajos terminados sin dueño.
El listado de proyectos devuelve como máximo 12; no significa que el resto se
haya borrado. Revisiones y originales pueden llenar el disco: reservar espacio
para al menos una copia completa, alertar al 70% y detener nuevas cargas al 85%.
Antes de reabrir revisar `df -h`, `docker stats --no-stream`, tamaño de volúmenes
y `docker compose ... logs --tail=100 api`. No borrar proyectos automáticamente
por fecha. Con servicios detenidos y copia verificada, un administrador puede
retirar una carpeta de proyecto completa identificada por sus 32 caracteres hex
con autorización del dueño. Nunca eliminar archivos internos sueltos.

Al reiniciar se pierden estados de cola/sondeo; un status puede devolver 404 aunque
existan archivos. Reabrir proyectos terminados desde la lista. Una conversión o
edición interrumpida puede dejar archivos parciales: conservar la carpeta y copia,
revisar informes/revisiones y reintentar desde el original en un proyecto nuevo;
si hay incoherencia, recuperar la copia completa. No hay recuperación transaccional
garantizada ante corte de corriente en todas las rutas de escritura.
Las generaciones en curso no se reanudan; el proveedor pudo facturar antes del
corte. No reintentar a ciegas. Las imágenes temporales terminadas tienen retención
en memoria (`KEEP_JOBS` en image_gen.py); tras reinicio no hay catálogo recuperable,
y pueden quedar archivos huérfanos en `_images`. Descargar/guardar resultados en
la biblioteca del navegador antes de mantenimiento. Tras backup y parada se puede
archivar y vaciar `_images`; se perderá acceso temporal, no la biblioteca IndexedDB.

## Verificar antes de admitir usuarios

```sh
sh deploy/ops.sh status
curl -I http://DOMINIO/             # redirección al HTTPS del mismo dominio
curl -I https://DOMINIO/guia.html   # certificado válido; sin -k
python3 deploy/smoke.py https://DOMINIO --user operador --other-user verificador \
  --drawing samples/block_2d.dxf
```

El smoke verifica guía, modelo de ejemplo, WASM, salud, modo compartido, login,
conversión/sondeo/descarga, rechazo de archivos internos, aislamiento del segundo
usuario y logout. Crea un proyecto de prueba persistente. Ejecutarlo desde otra
máquina; sin `--user` solo comprueba recursos y barrera anónima. Luego, en navegador:
abrir guía, confirmar fuentes sin errores de red, cargar ejemplos, importar y
exportar GLB Draco y Meshopt, reabrirlos, revisar materiales/vistas y descargar una
imagen. No se llama a proveedores de pago en pruebas automatizadas.
Probar 413 con un archivo de más de 100 MiB, saturación 503, CORS ajeno y que 8765
es inaccesible externamente. Guardar la evidencia de estas pruebas y del ensayo
de restauración. `health` es liveness: no garantiza DWG, proveedores ni disco libre.

## Copia, actualización y restauración

`sh deploy/ops.sh backup` detiene web/API, copia **juntos** cuentas y proyectos,
crea SHA-256, registra `.env` y las imágenes, y arranca la misma release. Elegir
ventana de mantenimiento; detener admisión y esperar a conversiones/imágenes antes
de parar (la espera de Docker de 30 s no drena tareas). Si falla, deja los servicios
parados y el archivo `.partial` para diagnóstico. No usarlo como copia válida.
Los archivos contienen originales, nombres y hashes de contraseña: cifrar y
transferir a un destino independiente. Retención inicial: 7 diarias y 4 semanales,
ajustada al espacio y obligaciones del operador; limpieza manual tras verificar
que sigue habiendo copias probadas. No se incluyen secretos ni datos del navegador.

Para actualizar: backup completo, registrar tag/commit e imágenes antiguas,
modificar `EXTRUDE_RELEASE` con etiqueta nueva, preparar código, `ops.sh build`,
`ops.sh check`, `ops.sh up`, smoke y comprobación visual. No ejecutar `down -v`,
`volume prune` ni sobrescribir etiquetas previas. Retener imágenes aprobadas con
`docker image save` o un almacén privado autorizado; no basta el tag local si se
ha borrado. Tags base fijados y lockfiles evitan actualizaciones indiscriminadas,
pero los tags y paquetes Debian pueden cambiar: para reproducibilidad binaria
registrar/fijar digests de las cuatro bases y conservar las imágenes resultantes.
Revisar avisos de seguridad y probar nuevas bases antes de sustituirlas.

Restaurar primero en **otro servidor o proyecto Compose aislado** con puertos
libres, dominio/entorno de ensayo y volúmenes vacíos. Obtener código e imágenes de
la release de la copia, configurar `.env`, restaurar secretos con UID 10001 y
copiar el `.tar.gz` y `.sha256` a `deploy/backups/`. Desde `deploy/`, el argumento
del comando es relativo a ese directorio:

```sh
sh deploy/ops.sh restore backups/ARCHIVO.tar.gz
sh deploy/ops.sh up
python3 deploy/smoke.py https://DOMINIO_ENSAYO --user operador
```

La restauración rechaza volúmenes no vacíos, valida SHA-256 y filtra rutas/enlaces
del tar; no mezcla backups con datos existentes. Si la extracción falla a medias,
conservar la evidencia y preparar volúmenes de ensayo nuevos antes de reintentar.
Comprobar proyectos reales, revisiones, permisos y una exportación, no solo health.
Para rollback de código sin cambio de datos: volver a las imágenes previas y
etiqueta anterior, `up --no-build` y smoke. Si hubo cambio de formato o corrupción,
restaurar copia en volúmenes nuevos con la release correspondiente; documentar
qué escrituras posteriores se perderían antes de cambiar el servicio activo.

## Referencias oficiales consultadas

Consultadas el 13-11-2026: [uv y Docker](https://docs.astral.sh/uv/guides/integration/docker/),
[uv 0.13.7](https://github.com/astral-sh/uv/releases/tag/0.13.7),
[Node 22.22.0](https://nodejs.org/en/blog/release/v22.22.0),
[Python 3.12.13](https://www.python.org/downloads/release/python-31213/),
[Caddy 2.10.2](https://github.com/caddyserver/caddy/releases/tag/v2.10.2),
[Compose services](https://docs.docker.com/reference/compose-file/services/),
[secretos Compose](https://docs.docker.com/compose/how-tos/use-secrets/),
[Caddy HTTPS](https://caddyserver.com/docs/automatic-https),
[reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy),
[request_body](https://caddyserver.com/docs/caddyfile/directives/request_body).
