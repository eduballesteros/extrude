# Lanzador de Extrude para Windows: arranca Docker Desktop si hace falta, levanta Extrude y lo
# abre en una ventana propia (modo aplicación de Edge o Chrome). Lo usa el acceso directo
# «Extrude» que crea «Instalar Extrude.bat». Compatible con Windows PowerShell 5.1.

# 'Continue': en PowerShell 5.1 lo que Docker escribe por stderr (p. ej. «Container …
# Starting») se convertiría en un error que detiene el script. Los fallos se miran por el
# código de salida de cada orden.
$ErrorActionPreference = 'Continue'
$Url = 'http://localhost:8080'
$Health = "$Url/api/health"
$Root = Split-Path -Parent $PSScriptRoot

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# --- Ventanita de progreso ---------------------------------------------------------------------
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Extrude'
$form.Size = New-Object System.Drawing.Size(420, 140)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $true
$form.TopMost = $true
$icon = Join-Path $PSScriptRoot 'extrude.ico'
if (Test-Path $icon) { $form.Icon = New-Object System.Drawing.Icon($icon) }
$label = New-Object System.Windows.Forms.Label
$label.Location = New-Object System.Drawing.Point(20, 20)
$label.Size = New-Object System.Drawing.Size(370, 60)
$label.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$form.Controls.Add($label)
$form.Show()

function Set-Status([string]$text) {
    $label.Text = $text
    [System.Windows.Forms.Application]::DoEvents()
}

function Wait-Seconds([int]$seconds) {
    for ($i = 0; $i -lt ($seconds * 10); $i++) {
        Start-Sleep -Milliseconds 100
        [System.Windows.Forms.Application]::DoEvents()
    }
}

function Fail([string]$message) {
    $form.Close()
    [System.Windows.Forms.MessageBox]::Show($message, 'Extrude', 'OK', 'Error') | Out-Null
    exit 1
}

function Test-Extrude {
    try {
        $response = Invoke-WebRequest -Uri $Health -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Invoke-Docker([string[]]$Arguments) {
    $output = & docker @Arguments 2>&1 | ForEach-Object { "$_" }
    return [pscustomobject]@{ Code = $LASTEXITCODE; Output = $output }
}

function Test-Docker {
    return (Invoke-Docker @('info')).Code -eq 0
}

function Open-App {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    )
    $browser = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if ($browser) {
        # Ventana propia, sin barra de direcciones; siempre el mismo navegador, así las vistas
        # y la biblioteca (guardadas en el navegador) se conservan entre sesiones
        Start-Process -FilePath $browser -ArgumentList "--app=$Url", '--start-maximized'
    } else {
        Start-Process $Url
    }
}

# --- 1. Ya está en marcha: solo abrir ----------------------------------------------------------
Set-Status 'Abriendo Extrude…'
if (Test-Extrude) {
    $form.Close()
    Open-App
    exit 0
}

# --- 2. Docker Desktop ---------------------------------------------------------------------------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "No se encuentra Docker Desktop.`n`nInstálalo desde https://www.docker.com/products/docker-desktop/ y vuelve a abrir Extrude."
}
if (-not (Test-Docker)) {
    Set-Status "Arrancando Docker Desktop…`nLa primera vez puede tardar un par de minutos."
    $desktop = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $desktop) { Start-Process -FilePath $desktop }
    $ready = $false
    for ($i = 0; $i -lt 90; $i++) {
        if (Test-Docker) { $ready = $true; break }
        Wait-Seconds 2
    }
    if (-not $ready) {
        Fail "Docker Desktop no ha terminado de arrancar.`n`nÁbrelo desde el menú Inicio, espera a que diga que está en marcha y vuelve a abrir Extrude."
    }
}

# --- 3. Extrude ----------------------------------------------------------------------------------
Set-Status "Arrancando Extrude…`nLa primera vez se prepara todo y puede tardar varios minutos."
Push-Location $Root
try {
    $result = Invoke-Docker @('compose', 'up', '-d')
    if ($result.Code -ne 0) {
        Fail "No se ha podido arrancar Extrude.`n`n$($result.Output | Select-Object -Last 8 | Out-String)"
    }
} finally {
    Pop-Location
}
$ready = $false
for ($i = 0; $i -lt 120; $i++) {
    if (Test-Extrude) { $ready = $true; break }
    Wait-Seconds 2
}
if (-not $ready) {
    Fail "Extrude no responde todavía.`n`nEspera un minuto y vuelve a abrirlo. Si sigue igual, abre Docker Desktop y mira el estado de «extrude-local»."
}

$form.Close()
Open-App
