@echo off
setlocal EnableExtensions

set "INSTALL_DIR=%LOCALAPPDATA%\PlanningDDVOutlookConnector"
set "DESKTOP_LINK=%USERPROFILE%\Desktop\Planning DDV.url"

echo.
echo Planning DDV - desinstalacion del Outlook Connector
echo.
echo Se quitara el protocolo planningddv:// del usuario actual y se eliminara:
echo %INSTALL_DIR%
echo.
pause

reg delete "HKCU\Software\Classes\planningddv" /f >nul 2>nul

if exist "%DESKTOP_LINK%" del /f /q "%DESKTOP_LINK%" >nul 2>nul
if exist "%INSTALL_DIR%" rmdir /s /q "%INSTALL_DIR%" >nul 2>nul

echo.
echo Conector Planning DDV desinstalado.
echo.
pause
exit /b 0
