<#
Deja la IA lista para la demo: ejecútalo 1 hora antes de presentar.

  powershell -ExecutionPolicy Bypass -File deploy\preparar_demo.ps1

1. Continúa las lecturas de las 31 viviendas hasta ahora (ml_seed_demo_readings --extend), con los escenarios de
   la demo: fuga en NIC-0004, NIC-0019 y NIC-0030; falla de bomba en NIC-0024 y NIC-0025. No borra nada.
   La IA solo analiza viviendas con lecturas de las últimas 6 horas: por eso se corre poco antes.
2. Ejecuta lo mismo que el botón "Realizar predicciones" (sincroniza, entrena si hace falta, predice desabasto
   y fugas, y crea los avisos).
#>
param(
    [string]$Ip = '167.234.220.232',
    [string]$Llave = "$env:USERPROFILE\.ssh\oracle-smartdrop",
    [string]$Usuario = 'ubuntu'
)
$ErrorActionPreference = 'Stop'
$Llave = (Resolve-Path $Llave).Path
$viviendas = '1,2,3,4,5,6,' + ((9..33) -join ',')

$comando = "cd ~/SmartDrop/SmartDrop && ../.venv/bin/python manage.py ml_seed_demo_readings --viviendas $viviendas --extend && ../.venv/bin/python manage.py ml_run_full_pipeline"
& ssh -i $Llave -o StrictHostKeyChecking=accept-new "$Usuario@$Ip" $comando
if ($LASTEXITCODE -ne 0) { throw 'La preparación falló; revisa el mensaje de arriba.' }
Write-Host "`nListo: abre Predicción de fugas y Predicción Suministro en el panel de administrador." -ForegroundColor Green
