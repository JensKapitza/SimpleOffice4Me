@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
cd /d "%ROOT%"

call :parse_args %*
if errorlevel 1 exit /b %errorlevel%

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
python "%ROOT%tools\launcher.py" start
exit /b %errorlevel%

:parse_args
if "%~1"=="" exit /b 0
if /I "%~1"=="--google-json" goto :google_json
if /I "%~1"=="--public-url" goto :public_url
if /I "%~1"=="--google-redirect-uri" goto :google_redirect_uri
if /I "%~1"=="--secret-key-file" goto :secret_key_file
if /I "%~1"=="--trusted-proxy-hops" goto :trusted_proxy_hops
if /I "%~1"=="--help" goto :help
if /I "%~1"=="-h" goto :help
echo Unbekannte Option: %~1
call :help
exit /b 2

:google_json
if "%~2"=="" goto :missing_value
set "SIMPLEOFFICE_GOOGLE_CREDENTIALS_FILE=%~2"
shift
shift
goto :parse_args

:public_url
if "%~2"=="" goto :missing_value
echo %~2| findstr /B /I "https://" >nul || (echo --public-url muss mit https:// beginnen.& exit /b 2)
set "PUBLIC_URL=%~2"
call set "LAST_CHAR=%%PUBLIC_URL:~-1%%"
if "%LAST_CHAR%"=="/" call set "PUBLIC_URL=%%PUBLIC_URL:~0,-1%%"
set "SIMPLEOFFICE_GOOGLE_REDIRECT_URI=%PUBLIC_URL%/auth/google/callback"
shift
shift
goto :parse_args

:google_redirect_uri
if "%~2"=="" goto :missing_value
set "SIMPLEOFFICE_GOOGLE_REDIRECT_URI=%~2"
shift
shift
goto :parse_args

:secret_key_file
if "%~2"=="" goto :missing_value
if not exist "%~2" (echo Session-Schluesseldatei fehlt: %~2& exit /b 2)
set /p SIMPLEOFFICE_SECRET_KEY=<"%~2"
if not defined SIMPLEOFFICE_SECRET_KEY (echo Session-Schluesseldatei ist leer: %~2& exit /b 2)
shift
shift
goto :parse_args

:trusted_proxy_hops
if "%~2"=="" goto :missing_value
echo %~2| findstr /R "^[0-9][0-9]*$" >nul || (echo Proxy-Anzahl muss eine ganze Zahl sein.& exit /b 2)
set "SIMPLEOFFICE_TRUSTED_PROXY_HOPS=%~2"
shift
shift
goto :parse_args

:missing_value
echo Option %~1 benoetigt einen Wert.
exit /b 2

:help
echo SimpleOffice4Me starten
echo.
echo Optionen:
echo   --google-json DATEI         Google OAuth JSON-Datei ^(Web-Anwendung^)
echo   --public-url URL            Oeffentliche HTTPS-Basis-URL; ueberschreibt JSON-Callback
echo   --google-redirect-uri URL   Vollstaendige Google OAuth Callback-URL
echo   --secret-key-file DATEI     Datei mit dauerhaftem Session-Schluessel
echo   --trusted-proxy-hops ANZAHL Anzahl vertrauenswuerdiger Reverse-Proxies
echo   --help                      Diese Hilfe anzeigen
echo.
echo Beispiel:
echo   start.bat --google-json C:\simpleoffice\google-oauth.json --trusted-proxy-hops 1
exit /b 0

:python_error
echo Python 3.10 oder neuer wurde nicht gefunden oder konnte keine virtuelle Umgebung erstellen.
exit /b 1

:install_error
echo Die Python-Abhaengigkeiten konnten nicht installiert werden.
exit /b 1
