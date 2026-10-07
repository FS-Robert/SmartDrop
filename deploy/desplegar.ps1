<#
Despliega SmartDrop en el servidor de Oracle Cloud desde tu PC con Windows (primera vez).

  powershell -ExecutionPolicy Bypass -File deploy\desplegar.ps1 `
      -Ip 129.146.10.20 -Llave C:\Users\rober\Downloads\ssh-key.key `
      -Dominio smartdrop-ugb.duckdns.org [-Correo tu@correo.com]

Qué hace:
  1. Clona (o actualiza) el repositorio de GitHub en el servidor.
  2. Sube lo que NO está en GitHub: .env, db.sqlite3, ml_timeseries.sqlite3, ml_models/ y media/.
     Si el servidor ya tiene datos, no los pisa (usa -ReemplazarDatos para forzarlo).
  3. Ejecuta deploy/instalar_servidor.sh (Python, Nginx, HTTPS, servicio).
#>
param(
    [Parameter(Mandatory = $true)] [string]$Ip,
    [Parameter(Mandatory = $true)] [string]$Llave,
    [Parameter(Mandatory = $true)] [string]$Dominio,
    [string]$Correo = '',   # opcional: avisos de vencimiento del certificado HTTPS
    [string]$Usuario = 'ubuntu',
    [switch]$ReemplazarDatos
)
$ErrorActionPreference = 'Stop'

$Repo = Split-Path -Parent $PSScriptRoot
$App = Join-Path $Repo 'SmartDrop'
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Destino = "$Usuario@$Ip"
$Llave = (Resolve-Path $Llave).Path

function Paso([string]$texto) { Write-Host "`n==> $texto" -ForegroundColor Cyan }

# El servidor clona desde GitHub: lo que no hayas subido con git push no llegará.
Push-Location $Repo
try {
    git fetch --quiet origin
    $pendiente = git status --porcelain --untracked-files=no
    $sinSubir = git rev-list --count '@{u}..HEAD'
    $remoteUrl = (git remote get-url origin).Trim()
} finally { Pop-Location }
if ($pendiente -or [int]$sinSubir -gt 0) {
    throw "Tienes cambios sin commit o sin push. Haz 'git commit' y 'git push' antes de desplegar."
}

# OpenSSH de Windows rechaza llaves privadas que otros usuarios pueden leer. Se usa el SID del usuario
# (no su nombre) porque si el equipo se llama igual que el usuario, "rober" apunta al equipo.
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
icacls $Llave /reset | Out-Null
icacls $Llave /inheritance:r /grant:r "*${sid}:(R)" | Out-Null

$SshOpts = @('-i', $Llave, '-o', 'StrictHostKeyChecking=accept-new', '-o', 'ServerAliveInterval=30')
function Remoto([string]$comando) {
    & ssh @SshOpts $Destino $comando
    if ($LASTEXITCODE -ne 0) { throw "Falló en el servidor: $comando" }
}
function Subir([string]$origen, [string]$destinoRemoto, [switch]$Carpeta) {
    $extra = @()
    if ($Carpeta) { $extra = @('-r') }
    & scp @SshOpts @extra $origen "${Destino}:$destinoRemoto"
    if ($LASTEXITCODE -ne 0) { throw "No se pudo subir $origen" }
}

Paso "Clonando el repositorio en el servidor"
Remoto "command -v git >/dev/null || (sudo apt-get update -y && sudo apt-get install -y git); test -d ~/SmartDrop/.git || git clone $remoteUrl ~/SmartDrop; cd ~/SmartDrop && git pull --ff-only"

$yaHayDatos = (& ssh @SshOpts $Destino 'test -f ~/SmartDrop/SmartDrop/db.sqlite3 && echo si') -eq 'si'
if ($yaHayDatos -and -not $ReemplazarDatos) {
    Paso "El servidor ya tiene datos: no se suben (usa -ReemplazarDatos para reemplazarlos)"
} else {
    Paso "Subiendo .env, bases de datos, modelos ML y adjuntos"
    Subir (Join-Path $Repo '.env') 'SmartDrop/.env'

    # Copia consistente aunque tengas el servidor local abierto (API de backup de SQLite).
    foreach ($db in 'db.sqlite3', 'ml_timeseries.sqlite3') {
        $origen = Join-Path $App $db
        $copia = Join-Path $env:TEMP "smartdrop-$db"
        & $Python -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()" $origen $copia
        if ($LASTEXITCODE -ne 0) { throw "No se pudo copiar $db" }
        Subir $copia "SmartDrop/SmartDrop/$db"
        Remove-Item $copia
    }
    foreach ($carpeta in 'ml_models', 'media') {
        $origen = Join-Path $App $carpeta
        if (Test-Path $origen) { Subir $origen 'SmartDrop/SmartDrop/' -Carpeta }
    }
}

Paso "Instalando en el servidor (la primera vez tarda unos minutos)"
Remoto "bash ~/SmartDrop/deploy/instalar_servidor.sh '$Dominio' '$Correo'"

Write-Host "`nListo: https://$Dominio" -ForegroundColor Green
