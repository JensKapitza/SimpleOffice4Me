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

if [ ! -x "$VENV/bin/python" ]; then
  python -m venv --system-site-packages "$VENV"
fi

echo "Installiere SimpleOffice-Pythonpakete …"
"$VENV/bin/python" -m pip install --disable-pip-version-check \
  'Flask>=3.0,<4' 'beautifulsoup4>=4.12,<5' 'html5lib>=1.1,<2' \
  'reportlab>=4.0,<6' 'pypdf>=5.0,<7' 'waitress>=3.0,<4'
"$VENV/bin/python" -m pip install --disable-pip-version-check --no-deps --editable "$ROOT"

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
