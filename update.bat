@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
cd /d "%ROOT%"

git diff --quiet
if errorlevel 1 goto :local_changes
git diff --cached --quiet
if errorlevel 1 goto :local_changes
set "WAS_RUNNING=0"
set "SERVICE_PYTHON=%ROOT%.venv\Scripts\python.exe"
if not exist "%SERVICE_PYTHON%" set "SERVICE_PYTHON=python"
"%SERVICE_PYTHON%" "%ROOT%tools\service_control.py" status >nul 2>nul
if not errorlevel 1 (
  set "WAS_RUNNING=1"
  call "%ROOT%stop.bat"
  if errorlevel 1 exit /b %errorlevel%
)
git pull --ff-only
if errorlevel 1 exit /b %errorlevel%
if "%WAS_RUNNING%"=="1" (
  call "%ROOT%start.bat" %*
  exit /b %errorlevel%
)
echo Update abgeschlossen. SimpleOffice4Me war vorher gestoppt und bleibt gestoppt.
exit /b 0

:local_changes
echo Update abgebrochen: Es gibt lokale Aenderungen. Bitte erst committen oder sichern.
exit /b 1
