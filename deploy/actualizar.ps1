<#
Publica en el servidor lo último que subiste a GitHub. Primero haz git commit + git push.

  powershell -ExecutionPolicy Bypass -File deploy\actualizar.ps1 -Ip 129.146.10.20 -Llave C:\Users\rober\Downloads\ssh-key.key

Los datos del servidor (.env, bases SQLite, modelos y adjuntos) no se tocan.
#>
param(
    [Parameter(Mandatory = $true)] [string]$Ip,
    [Parameter(Mandatory = $true)] [string]$Llave,
    [string]$Usuario = 'ubuntu'
)
$ErrorActionPreference = 'Stop'
$Llave = (Resolve-Path $Llave).Path

& ssh -i $Llave -o StrictHostKeyChecking=accept-new "$Usuario@$Ip" 'bash ~/SmartDrop/deploy/actualizar.sh'
if ($LASTEXITCODE -ne 0) { throw 'La actualización falló; revisa el mensaje de arriba.' }
