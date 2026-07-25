@echo off
setlocal
"%LOCALAPPDATA%\PlanningDDVOutlookConnector\planning_ddv_outlook_bridge.exe" "%~1"
if errorlevel 1 (
  echo.
  echo Planning DDV Outlook Bridge fallo.
  echo Revise el log:
  echo %LOCALAPPDATA%\PlanningDDVOutlookConnector\connector.log
  echo.
  pause
)
