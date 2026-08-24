@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=run"
"%PYTHON_EXE%" tools\sftp_setup.py "%ACTION%"
exit /b %ERRORLEVEL%
