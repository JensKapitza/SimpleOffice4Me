#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"
UPDATER_PYTHON="${PYTHON:-python3}"
if [ -x "$ROOT/.venv/bin/python" ]; then
  UPDATER_PYTHON="$ROOT/.venv/bin/python"
fi
WAS_RUNNING=0
if "$UPDATER_PYTHON" "$ROOT/tools/service_control.py" status >/dev/null 2>&1; then
  WAS_RUNNING=1
  "$ROOT/stop.sh"
fi
"$UPDATER_PYTHON" "$ROOT/tools/release_updater.py" --root "$ROOT"
if [ "$WAS_RUNNING" -eq 1 ]; then
  exec "$ROOT/start.sh" "$@"
fi
echo "Update abgeschlossen. SimpleOffice4Me war vorher gestoppt und bleibt gestoppt."
