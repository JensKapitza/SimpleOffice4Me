#!/usr/bin/env bash
# Linux starter. It owns only the local .venv in this project directory.
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PYTHON="${PYTHON:-python3}"
VENV="$ROOT/.venv"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3 wurde nicht gefunden. Bitte Python 3.10 oder neuer installieren." >&2
  exit 1
fi

if [ ! -x "$VENV/bin/python" ]; then
  "$PYTHON" -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --disable-pip-version-check --editable "$ROOT"
exec "$VENV/bin/python" "$ROOT/tools/launcher.py" start "$@"
