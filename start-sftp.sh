#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"

PYTHON_BOOTSTRAP="${PYTHON:-python3}"
VENV="$ROOT/.venv"
VENV_PYTHON="$VENV/bin/python"
IS_TERMUX=0

if [ -n "${TERMUX_VERSION:-}" ] \
  || { [ -n "${PREFIX:-}" ] && [ -x "${PREFIX}/bin/pkg" ]; } \
  || [ -x /data/data/com.termux/files/usr/bin/pkg ] \
  || command -v termux-info >/dev/null 2>&1; then
  IS_TERMUX=1
fi

if ! command -v "$PYTHON_BOOTSTRAP" >/dev/null 2>&1; then
  echo "Python 3 wurde nicht gefunden." >&2
  exit 1
fi

if [ "$IS_TERMUX" -eq 1 ]; then
  echo "Termux erkannt: SFTP-Kryptografie wird über pkg bereitgestellt."
  pkg install -y python-cryptography python-bcrypt python-pynacl

  if [ -x "$VENV_PYTHON" ] && ! grep -Eiq '^include-system-site-packages[[:space:]]*=[[:space:]]*true$' "$VENV/pyvenv.cfg"; then
    echo "Vorhandene .venv isoliert Termux-Pakete; erstelle sie mit Systempaketen neu."
    rm -rf "$VENV"
  fi
fi

if [ ! -x "$VENV_PYTHON" ]; then
  if [ "$IS_TERMUX" -eq 1 ]; then
    "$PYTHON_BOOTSTRAP" -m venv --system-site-packages "$VENV"
  else
    "$PYTHON_BOOTSTRAP" -m venv "$VENV"
  fi
fi

if [ "$IS_TERMUX" -eq 1 ]; then
  "$VENV_PYTHON" - <<'PY'
import importlib
for module in ("cryptography", "bcrypt", "nacl"):
    importlib.import_module(module)
print("Termux-SFTP-Kryptografie ist importierbar.")
PY

  # Paramiko selbst ist Python-Code. Seine nativen Abhängigkeiten kommen oben
  # aus Termux pkg; --no-deps verhindert einen PyNaCl-/bcrypt-Source-Build.
  "$VENV_PYTHON" -m pip install --disable-pip-version-check --only-binary=:all: --no-deps 'paramiko>=3.5,<6'
  "$VENV_PYTHON" -m pip install --disable-pip-version-check --no-deps --editable "$ROOT"
  "$VENV_PYTHON" -m pip check
else
  # SFTP must run with the same project-owned virtual environment as SimpleOffice.
  # Re-installing the editable extra is idempotent and adds Paramiko only when
  # this explicit SFTP starter is used.
  "$VENV_PYTHON" -m pip install --disable-pip-version-check --editable "$ROOT[sftp]"
fi

exec "$VENV_PYTHON" tools/sftp_setup.py "${1:-run}"
