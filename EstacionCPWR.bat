@echo off
rem Estacion terrestre CPWR: doble clic para abrir el dashboard en el PC.
chcp 65001 >nul
title Estacion CPWR
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo No se encontro Python. Instalalo desde https://www.python.org y marca "Add python.exe to PATH".
    pause
    exit /b 1
)

rem la primera vez en un PC nuevo instala las librerias
python -c "import aiohttp, numpy" >nul 2>nul
if errorlevel 1 (
    echo Instalando librerias, solo la primera vez...
    python -m pip install -r requirements.txt
    if errorlevel 1 ( echo No se pudieron instalar las librerias. & pause & exit /b 1 )
)

echo.
echo  ==========================================
echo    ESTACION CPWR
echo  ==========================================
echo    1. Cohete real   (conecta antes el PC a la red Cohete_ESP32)
echo    2. Simulacion    (prueba sin cohete)
echo    3. Guardar mapa de Caldas (con internet, antes de ir a campo)
echo  ==========================================
choice /c 123 /n /m "  Elige 1, 2 o 3: "
set OPCION=%errorlevel%

if %OPCION%==3 (
    python descargar_mapa.py --centro 5.0560,-75.4925
    pause
    exit /b 0
)

if %OPCION%==2 (
    start "Simulador ESP32 (cierra esta ventana para detenerlo)" /min python simulador_esp32.py --formato v2 --hz 20
)

rem abre el navegador cuando el servidor ya esta arriba
start "" /b cmd /c "timeout /t 3 /nobreak >nul & start http://localhost:8080"
echo.
echo  Dashboard en http://localhost:8080  ·  para cerrar: Ctrl+C o cierra esta ventana
echo.
python servidor.py
pause
