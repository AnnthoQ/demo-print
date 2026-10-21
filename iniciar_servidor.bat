@echo off
rem ============================================================================
rem  Inicia el servidor de monitoreo de impresoras en SEGUNDO PLANO (sin ventana).
rem
rem  Para que arranque con Windows: crea un acceso directo de este archivo y
rem  colocalo en la carpeta de inicio (Win+R, escribe  shell:startup  y Enter).
rem  En las propiedades del acceso directo elige  Ejecutar: Minimizada.
rem
rem  Registro: logs\servidor-bat.log     Detener: detener_servidor.bat
rem ============================================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"

rem Puerto: variable PORT del entorno, o el de .env, o 8787
set "PUERTO=8787"
if exist ".env" for /f "usebackq tokens=1,* delims==" %%a in (".env") do if /i "%%a"=="PORT" set "PUERTO=%%b"
if defined PORT set "PUERTO=%PORT%"

rem No lanzar un segundo servidor si ya hay uno (por ejemplo, el servicio de Windows)
netstat -ano | findstr /r /c:":%PUERTO% .*LISTENING" >nul
if not errorlevel 1 (
  echo El servidor ya esta en ejecucion en el puerto %PUERTO%. No se inicia otro.
  timeout /t 3 /nobreak >nul
  exit /b 0
)

if not exist ".env" (
  echo Falta el archivo .env ^(copia .env.example y completalo^).
  goto :error
)

rem pythonw.exe = Python sin ventana. Se ignora el acceso de la Microsoft Store (WindowsApps).
set "PYW="
for /f "delims=" %%p in ('where pythonw 2^>nul ^| findstr /v /i "WindowsApps"') do if not defined PYW set "PYW=%%p"
if not defined PYW (
  echo No se encontro pythonw.exe. Instala Python 3.10+ marcando "Add python.exe to PATH".
  goto :error
)

if not exist "logs" mkdir "logs"
set "LOG_FILE=%~dp0logs\servidor-bat.log"
set "PYTHONIOENCODING=utf-8"
rem Salida desviada: pythonw no debe heredar la consola que lo lanzo (los mensajes van a LOG_FILE)
start "" "%PYW%" "%~dp0server\servidor.py" <nul >nul 2>&1

rem Comprobar que quedo escuchando (pausa de 1 s con ping: timeout no funciona sin consola)
for /l %%i in (1,1,15) do (
  ping -n 2 127.0.0.1 >nul
  netstat -ano | findstr /r /c:":%PUERTO% .*LISTENING" >nul && goto :ok
)
echo El servidor no respondio. Revisa logs\servidor-bat.log
goto :error

:ok
echo Servidor de impresoras iniciado en segundo plano (puerto %PUERTO%).
timeout /t 2 /nobreak >nul
exit /b 0

:error
timeout /t 15
exit /b 1
