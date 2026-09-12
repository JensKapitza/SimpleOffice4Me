@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
cd /d "%ROOT%"
set "UPDATER_PYTHON=%ROOT%.venv\Scripts\python.exe"
if not exist "%UPDATER_PYTHON%" set "UPDATER_PYTHON=python"
set "WAS_RUNNING=0"
"%UPDATER_PYTHON%" "%ROOT%tools\service_control.py" status >nul 2>nul
if not errorlevel 1 (
  set "WAS_RUNNING=1"
  call "%ROOT%stop.bat"
  if errorlevel 1 exit /b %errorlevel%
)
"%UPDATER_PYTHON%" "%ROOT%tools\release_updater.py" --root "%ROOT%"
if errorlevel 1 exit /b %errorlevel%
if "%WAS_RUNNING%"=="1" (
  call "%ROOT%start.bat" %*
  exit /b %errorlevel%
)
echo Update abgeschlossen. SimpleOffice4Me war vorher gestoppt und bleibt gestoppt.
exit /b 0
