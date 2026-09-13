#!/usr/bin/env bash
# Starts the dedicated Mini Services worker (SIP/DNS/DHCP/TFTP/Gateway).
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
CONFIG="${SIMPLEOFFICE_MINI_SERVICES_CONFIG:-$ROOT/instance/mini-services.json}"

usage() {
  cat <<'EOF'
SimpleOffice4Me Mini Services starten

Optionen:
  --config DATEI   Alternative Mini-Services-Konfiguration
  --help            Diese Hilfe anzeigen

Der Worker startet die konfigurierten Sub-Dienste. SIP wird automatisch
mitgestartet; standardmäßig wird instance/mini-services.json verwendet.
Für DHCP/DNS auf privilegierten Ports oder Gateway/Routing können erhöhte
Rechte bzw. passende Linux-Capabilities erforderlich sein.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --config)
      [ "$#" -ge 2 ] || { echo "--config benötigt einen Dateipfad." >&2; exit 2; }
      CONFIG="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unbekannte Option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -n "${SIMPLEOFFICE_MINI_SERVICES_PYTHON:-}" ]; then
  PYTHON="$SIMPLEOFFICE_MINI_SERVICES_PYTHON"
elif [ -x "$ROOT/.venv/bin/python" ]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

if [[ "$PYTHON" == */* ]]; then
  [ -x "$PYTHON" ] || { echo "Python ist nicht ausführbar: $PYTHON" >&2; exit 1; }
elif ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python wurde nicht gefunden: $PYTHON" >&2
  echo "Starte einmal ./start.sh oder setze SIMPLEOFFICE_MINI_SERVICES_PYTHON." >&2
  exit 1
fi

if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
  echo "Für die Mini Services wird Python 3.10 oder neuer benötigt." >&2
  exit 1
fi

cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export SIMPLEOFFICE_MINI_SERVICES_CONFIG="$CONFIG"

echo "SimpleOffice4Me Mini Services werden gestartet."
echo "  Python: $PYTHON"
echo "  Konfiguration: $CONFIG"
echo "  Dienste: SIP sowie konfigurierte DNS/DHCP/TFTP/Gateway-Dienste"

exec "$PYTHON" -m tools.mini_services --config "$CONFIG"
