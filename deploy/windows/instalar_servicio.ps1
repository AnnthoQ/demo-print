# Registra el servidor de monitoreo como servicio de Windows usando NSSM (https://nssm.cc).
# Ejecutar en PowerShell COMO ADMINISTRADOR, desde la raíz del proyecto:
#     powershell -ExecutionPolicy Bypass -File deploy\windows\instalar_servicio.ps1
#
# Opciones:
#     -Subred 10.0.101.0/24   abre TCP 8787 en el firewall solo para esa subred (cualquier perfil de red)
#     -SinFirewall            no crea la regla (solo funcionará la extensión de este equipo)
#     -Nssm <ruta>            ruta a nssm.exe si no se encuentra sola
# Requisitos: Python 3.10+ y el archivo .env configurado (HOST=0.0.0.0 para acceso desde la red).
# Desinstalar:  nssm remove MonitorImpresoras confirm ; Remove-NetFirewallRule -DisplayName "Monitor de impresoras (8787)"

param(
    [string]$Nssm,
    [string]$Nombre = "MonitorImpresoras",
    [string]$Python = (Get-Command python -ErrorAction Stop).Source,
    [int]$Puerto = 8787,
    [string]$Subred = "10.0.101.0/24",
    [switch]$SinFirewall
)

$ErrorActionPreference = "Stop"
$esAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $esAdmin) { throw "Ejecuta este script en PowerShell abierto como administrador." }

$Raiz = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Logs = Join-Path $Raiz "logs"
if (-not (Test-Path (Join-Path $Raiz ".env"))) { throw "Falta $Raiz\.env (copia .env.example y complétalo)" }
if (-not (Test-Path (Join-Path $Raiz "config\printers.json"))) { throw "Falta $Raiz\config\printers.json" }

# NSSM: parámetro, PATH o la carpeta de paquetes de winget
if (-not $Nssm) {
    $cmd = Get-Command nssm -ErrorAction SilentlyContinue
    if ($cmd) { $Nssm = $cmd.Source }
    else {
        $Nssm = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter nssm.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\win64\\' } | Select-Object -First 1 -ExpandProperty FullName
    }
}
if (-not $Nssm -or -not (Test-Path $Nssm)) { throw "No se encontró nssm.exe. Instálalo con: winget install --id NSSM.NSSM -e" }

# El puerto debe estar libre (p. ej. un servidor iniciado a mano)
$ocupado = Get-NetTCPConnection -LocalPort $Puerto -State Listen -ErrorAction SilentlyContinue
if ($ocupado -and -not (Get-Service $Nombre -ErrorAction SilentlyContinue)) {
    throw "El puerto $Puerto ya está en uso (proceso $($ocupado[0].OwningProcess)). Detén ese servidor y vuelve a ejecutar."
}

# Reinstalación limpia si ya existía
if (Get-Service $Nombre -ErrorAction SilentlyContinue) {
    Write-Host "Reemplazando el servicio existente '$Nombre'..."
    & $Nssm stop $Nombre | Out-Null
    & $Nssm remove $Nombre confirm | Out-Null
    Start-Sleep -Seconds 1
}

New-Item -ItemType Directory -Force $Logs | Out-Null
& $Nssm install $Nombre $Python | Out-Null
& $Nssm set $Nombre AppParameters "server\servidor.py" | Out-Null
& $Nssm set $Nombre AppDirectory $Raiz | Out-Null
& $Nssm set $Nombre DisplayName "Monitor de impresoras" | Out-Null
& $Nssm set $Nombre Description "Consulta las impresoras por SNMP (solo lectura) y expone la API para la extensión de Chrome." | Out-Null
& $Nssm set $Nombre Start SERVICE_AUTO_START | Out-Null
& $Nssm set $Nombre AppEnvironmentExtra "PYTHONIOENCODING=utf-8" "PYTHONUNBUFFERED=1" | Out-Null
& $Nssm set $Nombre AppStdout (Join-Path $Logs "servidor.log") | Out-Null
& $Nssm set $Nombre AppStderr (Join-Path $Logs "servidor.log") | Out-Null
& $Nssm set $Nombre AppRotateFiles 1 | Out-Null
# Rotación solo al arrancar: la rotación en vivo (AppRotateOnline) dejaba el servicio colgado en
# "Stop Pending" al detenerlo, aunque Python ya había terminado
& $Nssm set $Nombre AppRotateOnline 0 | Out-Null
& $Nssm set $Nombre AppRotateBytes 5242880 | Out-Null
& $Nssm set $Nombre AppExit Default Restart | Out-Null
& $Nssm set $Nombre AppRestartDelay 5000 | Out-Null
& $Nssm start $Nombre | Out-Null

if (-not $SinFirewall) {
    $regla = "Monitor de impresoras ($Puerto)"
    Get-NetFirewallRule -DisplayName $regla -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    # Cualquier perfil (la red de la oficina figura como "Pública"), pero solo desde la subred local
    New-NetFirewallRule -DisplayName $regla -Direction Inbound -Protocol TCP -LocalPort $Puerto `
        -RemoteAddress $Subred -Profile Any -Action Allow | Out-Null
    Write-Host "Firewall: TCP $Puerto abierto solo para $Subred."
}

# Verificación
Start-Sleep -Seconds 4
$estado = (Get-Service $Nombre).Status
try {
    $salud = Invoke-RestMethod "http://127.0.0.1:$Puerto/health" -TimeoutSec 5
    Write-Host "Servicio '$Nombre': $estado. /health OK ($($salud.printers) impresoras)." -ForegroundColor Green
} catch {
    Write-Host "Servicio '$Nombre': $estado, pero /health no responde. Revisa $Logs\servidor.log" -ForegroundColor Yellow
}
$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -like "10.0.101.*" } | Select-Object -First 1).IPAddress
if ($ip) { Write-Host "URL para las extensiones de la oficina: http://${ip}:$Puerto" }
