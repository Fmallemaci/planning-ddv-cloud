@echo off
setlocal EnableExtensions

set "INSTALL_DIR=%LOCALAPPDATA%\PlanningDDVOutlookConnector"

if not exist "%INSTALL_DIR%\run_connector.cmd" (
  echo No se encontro run_connector.cmd en:
  echo %INSTALL_DIR%
  echo.
  echo Ejecute primero Instalar Planning DDV Outlook Connector.cmd.
  pause
  exit /b 1
)

reg add "HKCU\Software\Classes\planningddv" /ve /d "URL:Planning DDV Outlook Connector" /f
if errorlevel 1 goto :error
reg add "HKCU\Software\Classes\planningddv" /v "URL Protocol" /d "" /f
if errorlevel 1 goto :error
reg add "HKCU\Software\Classes\planningddv\shell\open\command" /ve /d "\"%INSTALL_DIR%\run_connector.cmd\" \"%%1\"" /f
if errorlevel 1 goto :error

echo Protocolo planningddv:// registrado para el usuario actual.
exit /b 0

:error
echo No se pudo registrar el protocolo planningddv://.
pause
exit /b 1
