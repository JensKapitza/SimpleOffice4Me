#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"

PYTHON_BOOTSTRAP="${PYTHON:-python3}"
VENV="$ROOT/.venv"
VENV_PYTHON="$VENV/bin/python"

if ! command -v "$PYTHON_BOOTSTRAP" >/dev/null 2>&1; then
  echo "Python 3 wurde nicht gefunden." >&2
  exit 1
fi

if [ ! -x "$VENV_PYTHON" ]; then
  "$PYTHON_BOOTSTRAP" -m venv "$VENV"
fi

# SFTP must run with the same project-owned virtual environment as SimpleOffice.
# Re-installing the editable extra is idempotent and upgrades an existing
# base-only venv by adding Paramiko when required.
"$VENV_PYTHON" -m pip install --disable-pip-version-check --editable "$ROOT[sftp]"

exec "$VENV_PYTHON" tools/sftp_setup.py "${1:-run}"
