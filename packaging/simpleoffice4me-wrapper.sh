#!/bin/sh
set -eu
APP_DIR=/opt/simpleoffice4me
VENV="$APP_DIR/.venv"
[ -x "$VENV/bin/python" ] || { echo "SimpleOffice4Me virtualenv fehlt: $VENV" >&2; exit 1; }
cd "$APP_DIR"
exec "$VENV/bin/python" -m tools.launcher "${@:-start}"
