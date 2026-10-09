#!/usr/bin/env bash
# Rebuild the project-local Python environment from a supported system runtime.
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
VENV="$ROOT/.venv"
PYTHON="${PYTHON:-python3}"

run_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "Root-Rechte werden für die Systempakete benötigt (sudo fehlt)." >&2
    return 126
  fi
}

# Pin the upstream source artifact; never execute unverified downloads.
UPSTREAM_VERSION=3.14.8
UPSTREAM_SHA256=c2215904f02b175596dc49351585104f4bc20341e1c47378b26a2c274360ce73
UPSTREAM_PREFIX="$ROOT/.python-runtime/$UPSTREAM_VERSION"

python_supported() {
  command -v "$1" >/dev/null 2>&1 && "$1" "$ROOT/simpleoffice_runtime_support.py" >/dev/null 2>&1
}

install_upstream_python() {
  if [ "$(uname -s)" != Linux ]; then
    echo "Der python.org-Quellcode-Build ist nur für Linux vorgesehen." >&2
    return 1
  fi
  if [ -x "$UPSTREAM_PREFIX/bin/python3.14" ] && python_supported "$UPSTREAM_PREFIX/bin/python3.14"; then
    PYTHON="$UPSTREAM_PREFIX/bin/python3.14"
    return 0
  fi
  echo "Installiere CPython $UPSTREAM_VERSION von python.org getrennt vom System ..."
  if command -v apt-get >/dev/null 2>&1; then
    run_root apt-get update
    run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      build-essential ca-certificates curl xz-utils libssl-dev zlib1g-dev libbz2-dev \
      libreadline-dev libsqlite3-dev libffi-dev liblzma-dev uuid-dev
  fi
  for tool in curl sha256sum tar make cc; do
    command -v "$tool" >/dev/null 2>&1 || { echo "Build-Werkzeug fehlt: $tool" >&2; return 1; }
  done
  temp="$(mktemp -d)"
  trap 'rm -rf -- "$temp"' EXIT
  curl --fail --location --proto '=https' --tlsv1.2 --retry 3 \
    "https://www.python.org/ftp/python/$UPSTREAM_VERSION/Python-$UPSTREAM_VERSION.tar.xz" \
    --output "$temp/Python.tar.xz"
  printf '%s  %s\n' "$UPSTREAM_SHA256" "$temp/Python.tar.xz" | sha256sum -c -
  tar -xJf "$temp/Python.tar.xz" -C "$temp"
  mkdir -p "$UPSTREAM_PREFIX"
  ( cd "$temp/Python-$UPSTREAM_VERSION"
    ./configure --prefix="$UPSTREAM_PREFIX" --with-ensurepip=install
    make -j2
    make altinstall
  )
  PYTHON="$UPSTREAM_PREFIX/bin/python3.14"
  python_supported "$PYTHON" || { echo "Upstream-Python erfüllt die Runtime-Policy nicht." >&2; return 1; }
  rm -rf -- "$temp"
  trap - EXIT
}

echo "SimpleOffice4Me: System- und Python-Abhängigkeiten prüfen ..."
if ! python_supported "$PYTHON"; then
  # Prefer an already installed, supported interpreter without modifying the OS.
  for candidate in python3.14 python3.13 python3.12 python3.11; do
    if python_supported "$candidate"; then
      PYTHON="$candidate"
      break
    fi
  done
fi
if ! python_supported "$PYTHON"; then
  install_upstream_python
fi
"$PYTHON" "$ROOT/simpleoffice_runtime_support.py"

# The requirements checker reports OS tools before the old environment is removed.
"$PYTHON" "$ROOT/tools/system_requirements.py" --missing-only || true

STAGING="$(mktemp -d "$ROOT/.venv-new.XXXXXX")"
BACKUP="$ROOT/.venv-old"
cleanup_staging() { [ ! -d "$STAGING" ] || rm -rf -- "$STAGING"; }
trap cleanup_staging EXIT

echo "Erzeuge und prüfe neue venv mit $("$PYTHON" --version 2>&1) ..."
rm -rf -- "$STAGING"
"$PYTHON" -m venv "$STAGING"
"$STAGING/bin/python" -m pip install --disable-pip-version-check --upgrade pip setuptools wheel
"$STAGING/bin/python" -m pip install --disable-pip-version-check --editable "$ROOT"
"$STAGING/bin/python" -m pip check
"$STAGING/bin/python" "$ROOT/simpleoffice_runtime_support.py"
"$STAGING/bin/python" "$ROOT/tools/system_requirements.py" --missing-only || true

rm -rf -- "$BACKUP"
if [ -d "$VENV" ]; then
  mv -- "$VENV" "$BACKUP"
fi
if ! mv -- "$STAGING" "$VENV"; then
  [ ! -d "$BACKUP" ] || mv -- "$BACKUP" "$VENV"
  echo "Neue venv konnte nicht aktiviert werden; vorherige Umgebung wurde wiederhergestellt." >&2
  exit 1
fi
trap - EXIT
rm -rf -- "$ROOT/.venv.bak"
echo "Cleanup abgeschlossen. start.sh verwendet jetzt: $VENV; vorherige Umgebung: $BACKUP"
