#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"

PYTHON_BOOTSTRAP="${PYTHON:-python3}"
VENV="$ROOT/.venv"
IS_TERMUX=0

if [ -n "${TERMUX_VERSION:-}" ] \
  || { [ -n "${PREFIX:-}" ] && [ -x "${PREFIX}/bin/pkg" ]; } \
  || [ -x /data/data/com.termux/files/usr/bin/pkg ] \
  || command -v termux-info >/dev/null 2>&1; then
  IS_TERMUX=1
fi

if [ "$IS_TERMUX" -eq 1 ]; then
  echo "Termux erkannt: Python und native SFTP-Abhängigkeiten werden über pkg bereitgestellt."
  # Python selbst gehört zum Bootstrap: start-sftp.sh muss auf einer frischen
  # Termux-Installation nicht bereits ein manuell eingerichtetes Python voraussetzen.
  pkg install -y python python-pip tzdata python-cryptography python-pillow python-bcrypt python-pynacl

  # Wenn der Android-Schnellstarter bereits eingerichtet wurde, dieselbe
  # projektlokale Umgebung weiterverwenden statt eine zweite Termux-venv anzulegen.
  if [ -x "$ROOT/.venv-android/bin/python" ]; then
    VENV="$ROOT/.venv-android"
  fi
fi

if ! command -v "$PYTHON_BOOTSTRAP" >/dev/null 2>&1; then
  echo "Python 3 wurde nicht gefunden." >&2
  exit 1
fi

VENV_PYTHON="$VENV/bin/python"

venv_uses_system_site_packages() {
  [ -f "$VENV/pyvenv.cfg" ] \
    && grep -Eiq '^include-system-site-packages[[:space:]]*=[[:space:]]*true$' "$VENV/pyvenv.cfg"
}

termux_venv_has_local_native_packages() {
  [ -x "$VENV_PYTHON" ] || return 1
  "$VENV_PYTHON" - "$VENV" <<'PY'
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
import sys

venv = Path(sys.argv[1]).resolve()
for name in ("cryptography", "Pillow", "bcrypt", "PyNaCl"):
    try:
        location = Path(distribution(name).locate_file("")).resolve()
    except PackageNotFoundError:
        continue
    if location == venv or venv in location.parents:
        print(f"lokale venv-Kopie erkannt: {name} ({location})")
        raise SystemExit(0)
raise SystemExit(1)
PY
}

if [ "$IS_TERMUX" -eq 1 ] && [ -x "$VENV_PYTHON" ]; then
  if ! venv_uses_system_site_packages \
    || ! "$VENV_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1 \
    || termux_venv_has_local_native_packages; then
    echo "Vorhandene Termux-venv ist isoliert, veraltet oder überschreibt native Pakete; erstelle sie sauber neu."
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
  # Auch ein späterer versehentlicher pip-Aufruf darf diese Android-nativen
  # Pakete niemals aus einem Source-Archiv bauen.
  export PIP_ONLY_BINARY="pynacl,bcrypt,cryptography"
  export PIP_PREFER_BINARY=1

  # Nicht nur importieren, sondern zusätzlich sicherstellen, dass die nativen
  # Distributionen tatsächlich aus Termux und nicht aus der projektlokalen venv kommen.
  "$VENV_PYTHON" - "$VENV" <<'PY'
import importlib
from importlib.metadata import distribution, version
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

venv = Path(sys.argv[1]).resolve()
packages = (
    ("cryptography", "cryptography"),
    ("Pillow", "PIL"),
    ("bcrypt", "bcrypt"),
    ("PyNaCl", "nacl"),
)
for distribution_name, module_name in packages:
    importlib.import_module(module_name)
    location = Path(distribution(distribution_name).locate_file("")).resolve()
    if location == venv or venv in location.parents:
        raise SystemExit(
            f"{distribution_name} kommt aus der lokalen venv statt aus Termux: {location}"
        )
    print(f"  {distribution_name} {version(distribution_name)}: native Termux-Installation")

ZoneInfo("Europe/Berlin")
print("  tzdata: Europe/Berlin verfügbar")
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

  # Paramiko bleibt dependency-isoliert: seine nativen Pakete kommen oben aus pkg.
  "$VENV_PYTHON" -m pip install --disable-pip-version-check --only-binary=:all: 'invoke>=2.0'
  "$VENV_PYTHON" -m pip install --disable-pip-version-check --only-binary=:all: --no-deps 'paramiko>=3.5,<6'
  "$VENV_PYTHON" -m pip install --disable-pip-version-check --no-deps --editable "$ROOT"
  "$VENV_PYTHON" -m pip check

  # pip check prüft Metadaten; dieser Smoke-Test prüft zusätzlich den echten Importpfad.
  "$VENV_PYTHON" - <<'PY'
from importlib.metadata import version
import invoke
import paramiko

print(f"Paramiko {version('paramiko')} / Invoke {version('invoke')} importierbar; SFTP-Abhängigkeiten vollständig.")
PY
else
  "$VENV_PYTHON" -m pip install --disable-pip-version-check --editable "$ROOT[sftp]"
fi

exec "$VENV_PYTHON" tools/sftp_setup.py "${1:-run}"