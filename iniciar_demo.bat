@echo off
rem Arranca todo lo de la PC para la demo del Laboratorio Final Integrador
rem Antes: hotspot del iPhone prendido, PC conectada a el y USB del Mega enchufado

rem Pide permisos de administrador (hacen falta para parar el servicio de Mosquitto)
fltmc >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

set "PCL=%~dp003 Repositorio\pc_local"
set "MOSQ=C:\Program Files\mosquitto"
set "DASH=https://utecnologica.org/estudiantes/imel/g06/"

echo.
echo === Demo Laboratorio Final - Grupo 06 ===
echo.

rem Revisa que la PC tenga la IP que esta cargada en el ESP8266
ipconfig | findstr /C:"172.20.10.4" >nul
if errorlevel 1 (
  echo ATENCION: la PC no tiene la IP 172.20.10.4.
  echo Revisa que este conectada al hotspot. Si la IP cambio, hay que
  echo cambiarla en secretos.h y volver a cargar el ESP8266.
  echo.
  ipconfig | findstr /C:"IPv4"
  echo.
  pause
) else (
  echo IP correcta: 172.20.10.4
)

rem Para el servicio de Mosquitto (solo escucha en la propia PC)
net stop mosquitto >nul 2>&1

rem 1) Mosquitto con la configuracion del proyecto
start "1 - Mosquitto" /D "%PCL%" cmd /k ""%MOSQ%\mosquitto.exe" -c mosquitto.conf -v"
timeout /t 3 /nobreak >nul

rem 2) Puente PC local - nube
start "2 - PC local" /D "%PCL%" cmd /k "python pc_local.py"
timeout /t 2 /nobreak >nul

rem 3) Valores en vivo que publica el ESP8266
start "3 - Telemetria en vivo" cmd /k ""%MOSQ%\mosquitto_sub.exe" -h localhost -t "utec/lab3/#" -v"

rem 4) Monitor en vivo local (http://localhost:8765)
rem    lee el puerto serie del Mega: instala pyserial si falta
python -c "import serial" >nul 2>&1 || python -m pip install pyserial
start "4 - Monitor en vivo" /D "%PCL%" cmd /k "python monitor_vivo.py"
timeout /t 2 /nobreak >nul
start "" "http://localhost:8765"

rem 5) Dashboard en la nube
start "" "%DASH%"

echo.
echo Listo. Quedaron abiertas 4 ventanas, el monitor en vivo y el dashboard.
echo Para cerrar todo, cerra las ventanas (en "2 - PC local" usa Ctrl+C primero).
echo.
timeout /t 8
