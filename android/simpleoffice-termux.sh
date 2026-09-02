#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

SCRIPT="$(readlink -f "$0")"
ROOT="$(CDPATH= cd -- "$(dirname -- "$SCRIPT")/.." && pwd)"
PYTHON="$ROOT/.venv-android/bin/python"
RUN_DIR="$ROOT/instance/run"
LOG="$RUN_DIR/android-web.log"
URL="http://127.0.0.1:8080"
ACTION="${1:-start}"

if [ ! -x "$PYTHON" ]; then
  echo "Android-Setup fehlt. Zuerst ausführen: bash android/setup-termux.sh" >&2
  exit 1
fi

case "$ACTION" in
  start)
    if "$PYTHON" "$ROOT/tools/service_control.py" status >/dev/null 2>&1; then
      echo "SimpleOffice läuft bereits unter $URL"
      command -v termux-open-url >/dev/null 2>&1 && termux-open-url "$URL" || true
      exit 0
    fi
    mkdir -p "$RUN_DIR"
    command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock || true
    cd "$ROOT"
    SIMPLEOFFICE_HOST=127.0.0.1 \
    SIMPLEOFFICE_PORT=8080 \
    SIMPLEOFFICE_WSGI_THREADS=2 \
    SIMPLEOFFICE_BACKGROUND_INDEX=0 \
    SIMPLEOFFICE_OSM_INDEX=0 \
    SIMPLEOFFICE_DATALOGGER=0 \
      nohup "$PYTHON" -m tools.launcher start >>"$LOG" 2>&1 &
    pid=$!
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      if ! kill -0 "$pid" 2>/dev/null; then
        echo "Serverstart fehlgeschlagen. Protokoll: $LOG" >&2
        tail -n 30 "$LOG" >&2 || true
        exit 1
      fi
      if "$PYTHON" -c "from urllib.request import urlopen; urlopen('$URL/auth/login', timeout=1)" >/dev/null 2>&1; then
        echo "SimpleOffice läuft unter $URL"
        command -v termux-open-url >/dev/null 2>&1 && termux-open-url "$URL" || true
        exit 0
      fi
      sleep 1
    done
    echo "Der Server antwortet noch nicht. Protokoll: $LOG" >&2
    exit 1
    ;;
  stop)
    "$PYTHON" "$ROOT/tools/service_control.py" stop
    command -v termux-wake-unlock >/dev/null 2>&1 && termux-wake-unlock || true
    ;;
  status)
    "$PYTHON" "$ROOT/tools/service_control.py" status
    ;;
  log)
    tail -n 100 "$LOG"
    ;;
  *)
    echo "Verwendung: simpleoffice {start|stop|status|log}" >&2
    exit 2
    ;;
esac
