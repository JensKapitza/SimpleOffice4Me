#!/usr/bin/env bash
# Install every dependency needed to build the SimpleOffice4Me .deb package.
# Supported host systems: Debian and Ubuntu (including derivatives using apt).
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

usage() {
  cat <<'EOF'
SimpleOffice4Me Build-Abhaengigkeiten installieren

Aufruf:
  ./build-dep.sh

Das Script installiert unter Debian/Ubuntu:
- Python 3, pip, venv und Python-Header
- Ruby + Header und fpm
- Compiler/Build-Werkzeuge
- OpenSSL/libffi/JPEG/zlib/freetype Header
- Rust/Cargo als Fallback fuer Python-Pakete ohne fertiges Wheel
- Debian-Paketwerkzeuge

Danach bauen mit:
  ./packaging/build-fpm.sh
EOF
}

case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
  "") ;;
  *)
    echo "Unbekannte Option: $1" >&2
    usage >&2
    exit 2
    ;;
esac

if ! command -v apt-get >/dev/null 2>&1; then
  echo "Dieses Script unterstuetzt derzeit Debian/Ubuntu-Systeme mit apt-get." >&2
  echo "Installiere die Build-Abhaengigkeiten dort manuell oder baue das Paket auf Debian/Ubuntu." >&2
  exit 2
fi

run_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "Root-Rechte werden benoetigt. Bitte sudo installieren oder das Script als root starten." >&2
    exit 2
  fi
}

PACKAGES=(
  ca-certificates
  git
  tar
  gzip
  dpkg-dev
  build-essential
  pkg-config
  python3
  python3-dev
  python3-pip
  python3-venv
  python3-setuptools
  python3-wheel
  ruby
  ruby-dev
  libffi-dev
  libssl-dev
  libjpeg-dev
  zlib1g-dev
  libfreetype6-dev
  rustc
  cargo
)

echo "SimpleOffice4Me: installiere Build-Abhaengigkeiten ..."
run_root apt-get update
run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${PACKAGES[@]}"

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
  echo "Python >= 3.10 wird benoetigt, installiert ist: $(python3 --version 2>&1 || true)" >&2
  exit 2
fi

if ! command -v gem >/dev/null 2>&1; then
  echo "RubyGems wurde trotz Ruby-Installation nicht gefunden." >&2
  exit 2
fi

if ! command -v fpm >/dev/null 2>&1; then
  echo "Installiere fpm ueber RubyGems ..."
  run_root gem install --no-document fpm
  hash -r
fi

if ! command -v fpm >/dev/null 2>&1; then
  # Debian/Ruby installations normally place the executable here. Check the
  # RubyGems bindir explicitly so a restricted sudo PATH gives a useful error.
  GEM_BINDIR="$(gem environment bindir 2>/dev/null || true)"
  if [ -n "$GEM_BINDIR" ] && [ -x "$GEM_BINDIR/fpm" ]; then
    echo "fpm wurde unter $GEM_BINDIR/fpm installiert, liegt aber nicht im PATH." >&2
    echo "Fuege $GEM_BINDIR zum PATH hinzu und starte den Build erneut." >&2
  else
    echo "fpm konnte nicht installiert oder gefunden werden." >&2
  fi
  exit 2
fi

printf '\nBuild-Umgebung ist bereit.\n'
printf '  Python: %s\n' "$(python3 --version 2>&1)"
printf '  fpm:    %s\n' "$(fpm --version 2>&1 | head -n 1)"
printf '\nPaket jetzt bauen mit:\n  cd %s\n  ./packaging/build-fpm.sh\n' "$ROOT"
