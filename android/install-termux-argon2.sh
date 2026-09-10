#!/data/data/com.termux/files/usr/bin/sh
set -eu

PYTHON="${1:-}"
if [ -z "$PYTHON" ] || [ ! -x "$PYTHON" ]; then
  echo "Aufruf: install-termux-argon2.sh /pfad/zur/python" >&2
  exit 2
fi

if [ -z "${PREFIX:-}" ] || [ ! -x "${PREFIX}/bin/pkg" ]; then
  echo "Der Argon2-Fallback ist ausschließlich für Termux vorgesehen." >&2
  exit 2
fi

argon2_works() {
  "$PYTHON" - <<'PY'
from importlib.metadata import PackageNotFoundError, version

try:
    import argon2
    import _argon2_cffi_bindings
    raw = version("argon2-cffi")
except (ImportError, PackageNotFoundError):
    raise SystemExit(1)

try:
    parts = tuple(int(part) for part in raw.split(".")[:2])
except ValueError:
    raise SystemExit(1)

raise SystemExit(0 if (23, 1) <= parts < (26, 0) else 1)
PY
}

if argon2_works; then
  "$PYTHON" -c 'from importlib.metadata import version; print(f"  argon2-cffi {version(chr(97)+chr(114)+chr(103)+chr(111)+chr(110)+chr(50)+chr(45)+chr(99)+chr(102)+chr(102)+chr(105))}: bereits verwendbar")'
  exit 0
fi

echo "Termux: installiere Argon2 getrennt von der normalen pip-Abhängigkeitsauflösung ..."
pkg install -y clang make cmake pkg-config libffi openssl argon2

# argon2-cffi-bindings benötigt beim Source-Build CFFI, setuptools-scm und wheel.
# Die Hilfspakete sind Python-only bzw. durch python-cryptography in Termux bereits
# verfügbar. Build-Isolation wird bewusst abgeschaltet, damit pip nicht versucht,
# CFFI erneut in einer isolierten Android-Build-Umgebung zu kompilieren.
"$PYTHON" -m pip install --disable-pip-version-check --prefer-binary \
  'setuptools>=68' 'setuptools-scm>=6.2' 'wheel>=0.41'

if ! "$PYTHON" -c 'import cffi' >/dev/null 2>&1; then
  echo "CFFI fehlt; baue es einmalig gegen Termux-libffi."
  "$PYTHON" -m pip install --disable-pip-version-check --no-build-isolation \
    'cffi>=1.12'
fi

# --ignore-installed verhindert, dass eine über --system-site-packages sichtbare
# ältere argon2-cffi-Version (z. B. 23.1.0) mit der gewünschten Version in
# denselben Resolver-Lauf gerät. --no-deps hält beide Installationen vollständig
# getrennt und vermeidet den beobachteten ResolutionImpossible-Fehler.
ARGON2_CFFI_USE_SYSTEM=1 ARGON2_CFFI_USE_SSE2=0 \
  "$PYTHON" -m pip install --disable-pip-version-check \
    --ignore-installed --no-deps --no-build-isolation \
    --no-binary=argon2-cffi-bindings \
    'argon2-cffi-bindings==25.1.0'

"$PYTHON" -m pip install --disable-pip-version-check \
  --ignore-installed --no-deps 'argon2-cffi==25.1.0'

if ! argon2_works; then
  echo "argon2-cffi wurde installiert, ist aber nicht importierbar." >&2
  exit 1
fi

"$PYTHON" - <<'PY'
from argon2 import PasswordHasher
from importlib.metadata import version

hasher = PasswordHasher()
encoded = hasher.hash("simpleoffice-termux-smoke-test")
if not hasher.verify(encoded, "simpleoffice-termux-smoke-test"):
    raise SystemExit("Argon2-Smoke-Test fehlgeschlagen")
print(f"  argon2-cffi {version('argon2-cffi')}: Source-Build gegen Termux-argon2 verwendbar")
PY
