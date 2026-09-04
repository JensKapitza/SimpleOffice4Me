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
  echo "Termux erkannt: native Python-/SFTP-Abhängigkeiten werden über pkg bereitgestellt."
  pkg install -y python-cryptography python-pillow python-bcrypt python-pynacl

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
  export PIP_ONLY_BINARY="PyNaCl,bcrypt,cryptography"
  export PIP_PREFER_BINARY=1

  "$VENV_PYTHON" - <<'PY'
import importlib
for module in ("cryptography", "PIL", "bcrypt", "nacl"):
    importlib.import_module(module)
print("Termux-native Python-/SFTP-Abhängigkeiten sind importierbar.")
PY

  TERMUX_RUNTIME_REQUIREMENTS="Flask>=3.0,<4 beautifulsoup4>=4.12,<5 reportlab>=4.0,<6 pypdf>=5.0,<7 waitress>=3.0,<4 watchdog>=6,<7"
  echo "Termux: versuche zuerst fertige Wheels für die gemeinsame Laufzeit ..."
  # shellcheck disable=SC2086
  if ! "$VENV_PYTHON" -m pip install --disable-pip-version-check --only-binary=:all: $TERMUX_RUNTIME_REQUIREMENTS; then
    echo "Nicht alle Laufzeitpakete besitzen ein kompatibles Android-Wheel; installiere Build-Werkzeuge für den kontrollierten Fallback."
    pkg install -y clang make pkg-config libffi openssl
    # shellcheck disable=SC2086
    "$VENV_PYTHON" -m pip install --disable-pip-version-check --prefer-binary $TERMUX_RUNTIME_REQUIREMENTS
  fi

  "$VENV_PYTHON" -m pip install --disable-pip-version-check --only-binary=:all: 'invoke>=2.0'
  "$VENV_PYTHON" -m pip install --disable-pip-version-check --only-binary=:all: --no-deps 'paramiko>=3.5,<6'
  "$VENV_PYTHON" -m pip install --disable-pip-version-check --no-deps --editable "$ROOT"
  "$VENV_PYTHON" -m pip check
else
  "$VENV_PYTHON" -m pip install --disable-pip-version-check --editable "$ROOT[sftp]"
fi

exec "$VENV_PYTHON" tools/sftp_setup.py "${1:-run}"
