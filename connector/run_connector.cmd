@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LOCALAPPDATA%\PlanningDDVOutlookConnector\planning_ddv_outlook_connector.ps1" %*
if errorlevel 1 (
  echo.
  echo Planning DDV Outlook Connector fallo.
  echo Revise el log:
  echo %LOCALAPPDATA%\PlanningDDVOutlookConnector\connector.log
  echo.
  pause
)
