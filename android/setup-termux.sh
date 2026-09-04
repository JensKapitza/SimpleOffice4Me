#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
VENV="$ROOT/.venv-android"
cd "$ROOT"

if [ -z "${PREFIX:-}" ] || [ ! -x "${PREFIX}/bin/pkg" ]; then
  echo "Dieses Setup muss in Termux ausgeführt werden." >&2
  exit 1
fi

echo "Installiere Android-Systempakete …"
pkg update -y
pkg install -y python python-pip python-pillow python-cryptography git

venv_uses_system_site_packages() {
  [ -f "$VENV/pyvenv.cfg" ] \
    && grep -Eiq '^include-system-site-packages[[:space:]]*=[[:space:]]*true$' "$VENV/pyvenv.cfg"
}

if [ -x "$VENV/bin/python" ] && ! venv_uses_system_site_packages; then
  echo "Vorhandene Android-venv isoliert Termux-Pakete; erstelle sie kompatibel neu."
  rm -rf "$VENV"
fi

if [ ! -x "$VENV/bin/python" ]; then
  python -m venv --system-site-packages "$VENV"
fi

# Android-native Kryptografie und Pillow kommen aus pkg. Selbst wenn sich die
# Abhängigkeitsauflösung später ändert, darf pip diese Pakete nicht lokal bauen.
export PIP_ONLY_BINARY="cryptography"
export PIP_PREFER_BINARY=1

echo "Prüfe native Termux-Pakete …"
"$VENV/bin/python" - <<'PY'
import importlib
from importlib.metadata import version

for distribution, module in (("cryptography", "cryptography"), ("Pillow", "PIL")):
    importlib.import_module(module)
    print(f"  {distribution} {version(distribution)} importierbar")
PY

echo "Installiere SimpleOffice-Pythonpakete …"
TERMUX_RUNTIME_REQUIREMENTS=(
  'Flask>=3.0,<4'
  'beautifulsoup4>=4.12,<5'
  'reportlab>=4.0,<6'
  'pypdf>=5.0,<7'
  'waitress>=3.0,<4'
  'watchdog>=6,<7'
  'tzdata>=2024.1'
)

if ! "$VENV/bin/python" -m pip install --disable-pip-version-check --only-binary=:all: "${TERMUX_RUNTIME_REQUIREMENTS[@]}"; then
  echo "Nicht alle Web-Laufzeitpakete besitzen ein kompatibles Android-Wheel; installiere Build-Werkzeuge für den kontrollierten Fallback."
  pkg install -y clang make pkg-config libffi openssl
  "$VENV/bin/python" -m pip install --disable-pip-version-check --prefer-binary "${TERMUX_RUNTIME_REQUIREMENTS[@]}"
fi

"$VENV/bin/python" - <<'PY'
from zoneinfo import ZoneInfo
ZoneInfo("Europe/Berlin")
print("  tzdata: Europe/Berlin verfügbar")
PY

"$VENV/bin/python" -m pip install --disable-pip-version-check --no-deps --editable "$ROOT"
"$VENV/bin/python" -m pip check

# Keep the command executable even when the checkout came from an archive that
# did not preserve Unix mode bits.
printf '#!%s/bin/bash\nexec %s/bin/bash %q/android/simpleoffice-termux.sh "$@"\n' \
  "$PREFIX" "$PREFIX" "$ROOT" >"$PREFIX/bin/simpleoffice"
chmod 700 "$PREFIX/bin/simpleoffice"

if [ ! -f "$ROOT/instance/simpleoffice.json" ]; then
  printf '\n\n' | "$VENV/bin/python" -m tools.launcher setup
fi

echo "Setup abgeschlossen. SimpleOffice wird jetzt gestartet."
exec "$PREFIX/bin/simpleoffice" start
