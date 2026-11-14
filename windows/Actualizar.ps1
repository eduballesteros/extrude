# Actualiza Extrude a la última versión: descarga el código nuevo (si la carpeta es un clon de
# Git) y reconstruye. Los proyectos, las vistas y las claves se conservan.

# 'Continue': en PowerShell 5.1, lo que Docker o Git escriben por stderr no debe detener el
# script; los fallos se miran por el código de salida.
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot

Write-Host 'Actualizar Extrude' -ForegroundColor White
Push-Location $Root
try {
    if ((Test-Path (Join-Path $Root '.git')) -and (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Host ''
        Write-Host '» Descargando la versión nueva' -ForegroundColor Cyan
        & git pull --ff-only
        if ($LASTEXITCODE -ne 0) {
            Write-Host '  No se ha podido descargar: se reconstruye la versión que ya tienes.' -ForegroundColor Yellow
        }
    } else {
        Write-Host ''
        Write-Host 'Esta carpeta no es un clon de Git: si has descargado una versión nueva en .zip,'
        Write-Host 'sustituye la carpeta (conservando el archivo .env) y vuelve a ejecutar esto.'
    }
    Write-Host ''
    Write-Host '» Reconstruyendo Extrude (puede tardar unos minutos)' -ForegroundColor Cyan
    & docker compose up -d --build
    if ($LASTEXITCODE -eq 0) {
        Write-Host ''
        Write-Host 'Extrude está actualizado. Ábrelo desde el escritorio.' -ForegroundColor Green
    } else {
        Write-Host ''
        Write-Host 'No se ha podido reconstruir: ¿está Docker Desktop en marcha?' -ForegroundColor Red
    }
} finally {
    Pop-Location
}
Read-Host "`nPulsa Intro para cerrar"
