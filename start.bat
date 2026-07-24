@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
cd /d "%ROOT%"

where py >nul 2>nul
if %errorlevel%==0 (
  set "PYTHON=py -3"
) else (
  set "PYTHON=python"
)

if not exist ".venv\Scripts\python.exe" (
  %PYTHON% -m venv ".venv"
  if errorlevel 1 goto :python_error
)

call ".venv\Scripts\activate.bat"
python -m pip install --disable-pip-version-check --editable "%ROOT%"
if errorlevel 1 goto :install_error
python "%ROOT%tools\launcher.py" start %*
exit /b %errorlevel%

:python_error
echo Python 3.10 oder neuer wurde nicht gefunden oder konnte keine virtuelle Umgebung erstellen.
exit /b 1

:install_error
echo Die Python-Abhaengigkeiten konnten nicht installiert werden.
exit /b 1
