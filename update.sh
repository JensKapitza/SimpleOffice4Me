#!/usr/bin/env bash
# Update only fast-forward changes so local work is never overwritten.
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Update abgebrochen: Es gibt lokale Änderungen. Bitte erst committen oder sichern." >&2
  exit 1
fi

WAS_RUNNING=0
if "${PYTHON:-python3}" "$ROOT/tools/service_control.py" status >/dev/null 2>&1; then
  WAS_RUNNING=1
  "$ROOT/stop.sh"
fi
git pull --ff-only
if [ "$WAS_RUNNING" -eq 1 ]; then
  exec "$ROOT/start.sh" "$@"
fi
echo "Update abgeschlossen. SimpleOffice4Me war vorher gestoppt und bleibt gestoppt."
