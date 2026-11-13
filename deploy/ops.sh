#!/bin/sh
# Ejecutar con sh deploy/ops.sh desde cualquier directorio en Linux.
set -eu
cd "$(dirname "$0")"
umask 077
dc() { docker compose --env-file .env -f compose.yaml "$@"; }
case "${1:-help}" in
  check)
    dc config --quiet
    dc run --rm --no-deps --entrypoint caddy web validate --config /etc/caddy/Caddyfile
    ;;
  build) dc build --pull ;;
  user)
    test "$#" -eq 2 || { echo 'Uso: ops.sh user NOMBRE' >&2; exit 2; }
    dc run --rm --no-deps api users add "$2" --users /data/accounts/users.json
    ;;
  up) dc up -d --no-build ;;
  down) dc stop web api ;;
  status) dc ps ;;
  logs) dc logs --tail=100 api web ;;
  backup)
    mkdir -p backups
    archive="backups/extrude-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
    test ! -e "$archive"
    dc stop web api
    # Ante fallo, se conservan los servicios parados: investigar antes de reabrir escrituras.
    dc run --rm -T --no-deps --entrypoint python api /opt/extrude/maintenance.py backup > "$archive.partial"
    mv "$archive.partial" "$archive"
    sha256sum "$archive" > "$archive.sha256"
    cp .env "$archive.release.env"
    dc images > "$archive.images.txt"
    dc up -d --no-build
    echo "Copia: deploy/$archive (guardar también configuración y secretos por separado)"
    ;;
  restore)
    test "$#" -eq 2 || { echo 'Uso: ops.sh restore backups/ARCHIVO.tar.gz' >&2; exit 2; }
    sha256sum -c "$2.sha256"
    dc stop web api
    dc run --rm -T --no-deps --entrypoint python api /opt/extrude/maintenance.py restore < "$2"
    echo 'Datos restaurados. Verifica la release y ejecuta ops.sh up y smoke.py.'
    ;;
  *) echo 'Uso: sh deploy/ops.sh check|build|user NOMBRE|up|down|status|logs|backup|restore ARCHIVO' ;;
esac
