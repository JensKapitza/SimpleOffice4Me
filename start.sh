#!/usr/bin/env bash
# Linux starter. It owns only the local .venv in this project directory.
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PYTHON="${PYTHON:-python3}"
VENV="$ROOT/.venv"

usage() {
  cat <<'EOF'
SimpleOffice4Me starten

Optionen:
  --google-json DATEI         Google OAuth JSON-Datei (Web-Anwendung)
  --public-url URL            Öffentliche HTTPS-Basis-URL; überschreibt die JSON-Callback-URI
  --google-redirect-uri URL   Vollständige Google OAuth Callback-URL
  --secret-key-file DATEI     Datei mit dauerhaftem SimpleOffice-Session-Schlüssel
  --trusted-proxy-hops ANZAHL Anzahl vertrauenswürdiger Reverse-Proxies
  --host ADRESSE              Bind-Adresse (Standard: 127.0.0.1)
  --port PORT                 HTTP-Port (Standard: aus Ersteinrichtung)
  --threads ANZAHL            Waitress-Worker-Threads (Standard: 4)
  --channel-timeout SEKUNDEN  Leerlaufzeit einer Verbindung (Standard: 120)
  --check-system              Systemwerkzeuge prüfen, ohne Serverstart
  --help                      Diese Hilfe anzeigen

Beispiel:
  ./start.sh --google-json /etc/simpleoffice/google-oauth.json \\
    --public-url https://office.example.de --trusted-proxy-hops 1
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --google-json)
      [ "$#" -ge 2 ] || { echo "--google-json benötigt einen Dateipfad." >&2; exit 2; }
      export SIMPLEOFFICE_GOOGLE_CREDENTIALS_FILE="$2"; shift 2 ;;
    --public-url)
      [ "$#" -ge 2 ] || { echo "--public-url benötigt eine URL." >&2; exit 2; }
      case "$2" in
        https://*) ;;
        *) echo "--public-url muss mit https:// beginnen." >&2; exit 2 ;;
      esac
      export SIMPLEOFFICE_GOOGLE_REDIRECT_URI="${2%/}/auth/google/callback"; shift 2 ;;
    --google-redirect-uri)
      [ "$#" -ge 2 ] || { echo "--google-redirect-uri benötigt eine URL." >&2; exit 2; }
      export SIMPLEOFFICE_GOOGLE_REDIRECT_URI="$2"; shift 2 ;;
    --secret-key-file)
      [ "$#" -ge 2 ] || { echo "--secret-key-file benötigt einen Dateipfad." >&2; exit 2; }
      [ -r "$2" ] || { echo "Session-Schlüsseldatei ist nicht lesbar: $2" >&2; exit 2; }
      IFS= read -r SIMPLEOFFICE_SECRET_KEY < "$2" || true
      [ -n "$SIMPLEOFFICE_SECRET_KEY" ] || { echo "Session-Schlüsseldatei ist leer: $2" >&2; exit 2; }
      export SIMPLEOFFICE_SECRET_KEY; shift 2 ;;
    --trusted-proxy-hops)
      [ "$#" -ge 2 ] || { echo "--trusted-proxy-hops benötigt eine Anzahl." >&2; exit 2; }
      case "$2" in *[!0-9]*|'') echo "Proxy-Anzahl muss eine ganze Zahl sein." >&2; exit 2 ;; esac
      [ "$2" -le 16 ] || { echo "Proxy-Anzahl muss zwischen 0 und 16 liegen." >&2; exit 2; }
      export SIMPLEOFFICE_TRUSTED_PROXY_HOPS="$2"; shift 2 ;;
    --host)
      [ "$#" -ge 2 ] || { echo "--host benötigt eine Adresse." >&2; exit 2; }
      [ -n "$2" ] || { echo "--host darf nicht leer sein." >&2; exit 2; }
      export SIMPLEOFFICE_HOST="$2"; shift 2 ;;
    --port)
      [ "$#" -ge 2 ] || { echo "--port benötigt eine Zahl." >&2; exit 2; }
      case "$2" in *[!0-9]*|'') echo "Port muss eine ganze Zahl sein." >&2; exit 2 ;; esac
      [ "$2" -ge 1 ] && [ "$2" -le 65535 ] || { echo "Port muss zwischen 1 und 65535 liegen." >&2; exit 2; }
      export SIMPLEOFFICE_PORT="$2"; shift 2 ;;
    --threads)
      [ "$#" -ge 2 ] || { echo "--threads benötigt eine Anzahl." >&2; exit 2; }
      case "$2" in *[!0-9]*|'') echo "Thread-Anzahl muss eine ganze Zahl sein." >&2; exit 2 ;; esac
      [ "$2" -ge 1 ] && [ "$2" -le 64 ] || { echo "Thread-Anzahl muss zwischen 1 und 64 liegen." >&2; exit 2; }
      export SIMPLEOFFICE_WSGI_THREADS="$2"; shift 2 ;;
    --channel-timeout)
      [ "$#" -ge 2 ] || { echo "--channel-timeout benötigt Sekunden." >&2; exit 2; }
      case "$2" in *[!0-9]*|'') echo "Timeout muss eine ganze Zahl sein." >&2; exit 2 ;; esac
      [ "$2" -ge 10 ] && [ "$2" -le 3600 ] || { echo "Timeout muss zwischen 10 und 3600 Sekunden liegen." >&2; exit 2; }
      export SIMPLEOFFICE_WSGI_CHANNEL_TIMEOUT="$2"; shift 2 ;;
    --check-system)
      exec "$PYTHON" "$ROOT/tools/system_requirements.py" ;;
    --help|-h)
      usage; exit 0 ;;
    *)
      echo "Unbekannte Option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3 wurde nicht gefunden. Bitte Python 3.10 oder neuer installieren." >&2
  exit 1
fi

"$PYTHON" "$ROOT/tools/system_requirements.py" --missing-only

if [ ! -x "$VENV/bin/python" ]; then
  "$PYTHON" -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --disable-pip-version-check --editable "$ROOT"
cd "$ROOT"
exec "$VENV/bin/python" -m tools.launcher start "$@"
