#!/bin/sh
set -eu
cd "$(dirname "$0")"
PYTHON="${PYTHON:-.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then PYTHON="${PYTHON_FALLBACK:-python3}"; fi
exec "$PYTHON" tools/sftp_setup.py "${1:-run}"
