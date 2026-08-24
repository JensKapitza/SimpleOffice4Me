#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PYTHON="$ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="${PYTHON_FALLBACK:-python3}"
exec "$PYTHON" "$ROOT/tools/service_control.py" stop "$@"
