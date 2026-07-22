@echo off
setlocal
set "INSTALL_DIR=%LOCALAPPDATA%\PlanningDDVOutlookConnector"
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
copy /Y "%~dp0planning_ddv_outlook_connector.ps1" "%INSTALL_DIR%\planning_ddv_outlook_connector.ps1" >nul
copy /Y "%~dp0run_connector.cmd" "%INSTALL_DIR%\run_connector.cmd" >nul

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root='HKCU:\Software\Classes\planningddv';" ^
  "New-Item -Path $root -Force | Out-Null;" ^
  "Set-Item -Path $root -Value 'URL:Planning DDV Outlook Connector';" ^
  "New-ItemProperty -Path $root -Name 'URL Protocol' -Value '' -PropertyType String -Force | Out-Null;" ^
  "$cmd=$root+'\shell\open\command';" ^
  "New-Item -Path $cmd -Force | Out-Null;" ^
  "$target='""%INSTALL_DIR%\run_connector.cmd"" ""%%1""';" ^
  "Set-Item -Path $cmd -Value $target;"

echo.
echo Planning DDV Outlook Connector instalado.
echo Protocolo registrado: planningddv://
echo.
pause
