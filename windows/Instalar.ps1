# Instalación de Extrude en Windows (una sola vez): accesos directos con icono, archivo de
# claves de IA y primera preparación. Se ejecuta con doble clic en «Instalar Extrude.bat».

# 'Continue': en PowerShell 5.1, lo que Docker o Git escriben por stderr no debe detener el
# script; los fallos se miran por el código de salida.
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
$Icon = Join-Path $PSScriptRoot 'extrude.ico'
$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

function Step([string]$text) { Write-Host ''; Write-Host "» $text" -ForegroundColor Cyan }

function New-Shortcut([string]$path, [string]$script, [string]$description, [bool]$hidden) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $PowerShell
    $style = if ($hidden) { '-WindowStyle Hidden ' } else { '' }
    $link.Arguments = "-NoProfile -ExecutionPolicy Bypass $style-File `"$(Join-Path $PSScriptRoot $script)`""
    $link.WorkingDirectory = $Root
    $link.IconLocation = "$Icon,0"
    $link.Description = $description
    $link.Save()
}

Write-Host 'Instalación de Extrude' -ForegroundColor White

# --- 1. Accesos directos -------------------------------------------------------------------------
Step 'Creando los accesos directos'
$desktop = [Environment]::GetFolderPath('Desktop')
$programs = Join-Path ([Environment]::GetFolderPath('Programs')) 'Extrude'
New-Item -ItemType Directory -Force -Path $programs | Out-Null
New-Shortcut (Join-Path $desktop 'Extrude.lnk') 'Extrude.ps1' 'Abrir Extrude' $true
New-Shortcut (Join-Path $programs 'Extrude.lnk') 'Extrude.ps1' 'Abrir Extrude' $true
New-Shortcut (Join-Path $programs 'Actualizar Extrude.lnk') 'Actualizar.ps1' 'Actualizar Extrude a la última versión' $false
Write-Host '  Escritorio y menú Inicio: «Extrude» (y «Actualizar Extrude» en el menú Inicio).'

# --- 2. Claves de IA -----------------------------------------------------------------------------
Step 'Claves de IA (opcional)'
$envFile = Join-Path $Root '.env'
if (Test-Path $envFile) {
    Write-Host '  Ya existe el archivo de claves (.env): se conserva.'
} else {
    Copy-Item (Join-Path $PSScriptRoot 'env.example') $envFile
    Write-Host '  Se ha creado el archivo de claves (.env).'
    $answer = Read-Host '  ¿Quieres abrirlo ahora para pegar las claves? (s/n)'
    if ($answer -match '^[sS]') {
        Start-Process notepad.exe -ArgumentList "`"$envFile`"" -Wait
    }
}

# --- 3. Docker y primera preparación -------------------------------------------------------------
Step 'Docker Desktop'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host '  Docker Desktop no está instalado.' -ForegroundColor Yellow
    Write-Host '  Instálalo desde https://www.docker.com/products/docker-desktop/ ,'
    Write-Host '  reinicia el ordenador si lo pide y después abre «Extrude» desde el escritorio.'
    Start-Process 'https://www.docker.com/products/docker-desktop/'
    Read-Host "`nPulsa Intro para cerrar"
    exit 0
}
$null = & docker info 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host '  Docker Desktop está instalado pero no está en marcha: arrancándolo…'
    $app = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $app) { Start-Process -FilePath $app }
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 2
        $null = & docker info 2>$null
        if ($LASTEXITCODE -eq 0) { break }
    }
}
$null = & docker info 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host '  Docker Desktop no responde todavía. Cuando esté en marcha, abre «Extrude».' -ForegroundColor Yellow
    Read-Host "`nPulsa Intro para cerrar"
    exit 0
}

Step 'Preparando Extrude (la primera vez tarda varios minutos)'
Push-Location $Root
try {
    & docker compose up -d --build
    $ok = $LASTEXITCODE -eq 0
} finally {
    Pop-Location
}
if (-not $ok) {
    Write-Host '  No se ha podido preparar Extrude: revisa los mensajes de arriba.' -ForegroundColor Red
    Read-Host "`nPulsa Intro para cerrar"
    exit 1
}

Write-Host ''
Write-Host 'Listo. Abre «Extrude» desde el escritorio o el menú Inicio.' -ForegroundColor Green
Start-Process -FilePath $PowerShell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$(Join-Path $PSScriptRoot 'Extrude.ps1')`""
Start-Sleep -Seconds 3
