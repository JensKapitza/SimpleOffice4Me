@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
cd /d "%ROOT%"

git diff --quiet
if errorlevel 1 goto :local_changes
git diff --cached --quiet
if errorlevel 1 goto :local_changes
git pull --ff-only
if errorlevel 1 exit /b %errorlevel%
call "%ROOT%start.bat" %*
exit /b %errorlevel%

:local_changes
echo Update abgebrochen: Es gibt lokale Aenderungen. Bitte erst committen oder sichern.
exit /b 1
