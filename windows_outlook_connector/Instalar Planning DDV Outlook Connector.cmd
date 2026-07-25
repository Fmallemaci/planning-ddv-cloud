@echo off
setlocal EnableExtensions

set "BASE_URL=https://planning-ddv-usuarios-prueba.onrender.com"
if not "%~1"=="" set "BASE_URL=%~1"
set "CONNECTOR_VERSION=1.0.0"
set "INSTALL_DIR=%LOCALAPPDATA%\PlanningDDVOutlookConnector"
set "DESKTOP_LINK=%USERPROFILE%\Desktop\Planning DDV.url"

echo.
echo Planning DDV - instalacion transparente del Outlook Connector
echo.
echo Origen de archivos: %~dp0
echo Destino: %INSTALL_DIR%
echo URL de Planning DDV: %BASE_URL%
echo.
echo Este instalador no descarga archivos, no usa permisos de administrador,
echo no crea tareas programadas y no modifica el inicio de Windows.
echo.
echo Hashes SHA-256 esperados del paquete:
type "%~dp0SHA256SUMS.txt"
echo.
echo Si desea verificar manualmente, compare estos valores antes de continuar.
echo.
pause

if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

copy /Y "%~dp0planning_ddv_outlook_bridge.exe" "%INSTALL_DIR%\planning_ddv_outlook_bridge.exe" >nul
if errorlevel 1 goto :error
copy /Y "%~dp0planning_ddv_outlook_bridge.cs" "%INSTALL_DIR%\planning_ddv_outlook_bridge.cs" >nul
if errorlevel 1 goto :error
copy /Y "%~dp0run_connector.cmd" "%INSTALL_DIR%\run_connector.cmd" >nul
if errorlevel 1 goto :error
copy /Y "%~dp0README_CONNECTOR.md" "%INSTALL_DIR%\README_CONNECTOR.md" >nul
copy /Y "%~dp0PRUEBA_APERTURA_BORRADOR.md" "%INSTALL_DIR%\PRUEBA_APERTURA_BORRADOR.md" >nul
copy /Y "%~dp0Registrar protocolo planningddv.cmd" "%INSTALL_DIR%\Registrar protocolo planningddv.cmd" >nul
copy /Y "%~dp0Desinstalar Planning DDV Outlook Connector.cmd" "%INSTALL_DIR%\Desinstalar Planning DDV Outlook Connector.cmd" >nul
copy /Y "%~dp0SHA256SUMS.txt" "%INSTALL_DIR%\SHA256SUMS.txt" >nul

> "%INSTALL_DIR%\version.txt" echo %CONNECTOR_VERSION%
> "%INSTALL_DIR%\base_url.txt" echo %BASE_URL%

echo.
echo Registrando protocolo planningddv:// para el usuario actual...
call "%INSTALL_DIR%\Registrar protocolo planningddv.cmd"
if errorlevel 1 goto :error

echo.
echo Creando acceso directo de escritorio...
> "%DESKTOP_LINK%" echo [InternetShortcut]
>> "%DESKTOP_LINK%" echo URL=%BASE_URL%

echo.
echo Planning DDV quedo configurado correctamente.
echo Version del conector: %CONNECTOR_VERSION%
echo Acceso directo: %DESKTOP_LINK%
echo.
pause
exit /b 0

:error
echo.
echo No se pudo configurar Planning DDV.
echo No se realizaron cambios de seguridad ni exclusiones del antivirus.
echo Revise que el ZIP este descomprimido en una carpeta local y vuelva a intentar.
echo.
pause
exit /b 1
