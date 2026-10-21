@echo off
rem Detiene el servidor de monitoreo iniciado con iniciar_servidor.bat.
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PUERTO=8787"
if exist ".env" for /f "usebackq tokens=1,* delims==" %%a in (".env") do if /i "%%a"=="PORT" set "PUERTO=%%b"
if defined PORT set "PUERTO=%PORT%"

rem Solo se cierra el servidor iniciado con iniciar_servidor.bat (pythonw.exe).
rem El servicio de Windows usa python.exe y nunca se toca desde aqui.
set "DETENIDO="
set "OTRO="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /r /c:":%PUERTO% .*LISTENING"') do (
  tasklist /fi "pid eq %%a" /fi "imagename eq pythonw.exe" | find /i "pythonw.exe" >nul && (taskkill /pid %%a /f >nul 2>&1 && set "DETENIDO=1") || set "OTRO=%%a"
)
if defined DETENIDO (
  echo Servidor detenido.
) else if defined OTRO (
  echo El puerto %PUERTO% lo usa otro proceso ^(PID %OTRO%^), probablemente el servicio de Windows.
  echo Para detener el servicio, en PowerShell como administrador:  Stop-Service MonitorImpresoras
) else (
  echo No habia ningun servidor en el puerto %PUERTO%.
)
timeout /t 5 /nobreak >nul
