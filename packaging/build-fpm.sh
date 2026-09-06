#!/usr/bin/env bash
# Build a self-contained SimpleOffice4Me Debian package with fpm.
# Python runtime dependencies are downloaded as wheels at build time and
# installed offline into /opt/simpleoffice4me/.venv by the package postinst.
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

PACKAGE_NAME="${SIMPLEOFFICE_PACKAGE_NAME:-simpleoffice4me}"
VERSION="${SIMPLEOFFICE_PACKAGE_VERSION:-$(python3 - <<'PY'
import tomllib
from pathlib import Path
print(tomllib.loads(Path('pyproject.toml').read_text(encoding='utf-8'))['project']['version'])
PY
)}"
ITERATION="${SIMPLEOFFICE_PACKAGE_ITERATION:-1}"
ARCH="${SIMPLEOFFICE_PACKAGE_ARCH:-$(dpkg --print-architecture 2>/dev/null || printf 'all')}"
OUT_DIR="${SIMPLEOFFICE_PACKAGE_OUT:-$ROOT/dist/packages}"
WORK_DIR="${SIMPLEOFFICE_PACKAGE_WORK:-$ROOT/build/fpm}"
STAGE="$WORK_DIR/root"
APP_DIR="$STAGE/opt/simpleoffice4me"
WHEELHOUSE="$APP_DIR/wheelhouse"

command -v fpm >/dev/null 2>&1 || {
  echo "fpm fehlt. Installation z.B.: gem install --no-document fpm" >&2
  exit 2
}
command -v python3 >/dev/null 2>&1 || { echo "python3 fehlt." >&2; exit 2; }
command -v tar >/dev/null 2>&1 || { echo "tar fehlt." >&2; exit 2; }
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "Python >= 3.10 wird zum Bauen benötigt." >&2
  exit 2
}

rm -rf "$WORK_DIR"
mkdir -p "$APP_DIR" "$WHEELHOUSE" "$OUT_DIR"

# Copy only application sources. Runtime state, VCS metadata and previous build
# artefacts must never enter a system package.
tar -C "$ROOT" -cf - \
  --exclude='./.git' \
  --exclude='./.venv' \
  --exclude='./instance' \
  --exclude='./build' \
  --exclude='./dist' \
  --exclude='./var' \
  --exclude='./database/*.sqlite' \
  --exclude='./database/*.sqlite-*' \
  --exclude='./__pycache__' \
  --exclude='*/__pycache__' \
  . | tar -C "$APP_DIR" -xf -

# Build/download every Python dependency now. The target machine therefore does
# not need PyPI access. Wheels are architecture-specific where required, which
# is why the resulting Debian package uses the builder's architecture.
python3 -m pip wheel \
  --wheel-dir "$WHEELHOUSE" \
  --disable-pip-version-check \
  "$ROOT"

install -D -m 0755 "$ROOT/packaging/simpleoffice4me-wrapper.sh" "$STAGE/usr/bin/simpleoffice4me"
install -D -m 0644 "$ROOT/packaging/simpleoffice4me.service" "$STAGE/lib/systemd/system/simpleoffice4me.service"
install -D -m 0644 "$ROOT/packaging/simpleoffice.env" "$STAGE/etc/simpleoffice4me/simpleoffice.env"
install -D -m 0644 "$ROOT/packaging/README-system-package.md" "$STAGE/usr/share/doc/simpleoffice4me/README.system-package.md"

# The post-install script owns the persistent target and creates this symlink.
rm -rf "$APP_DIR/instance"

fpm \
  -s dir \
  -t deb \
  -n "$PACKAGE_NAME" \
  -v "$VERSION" \
  --iteration "$ITERATION" \
  --architecture "$ARCH" \
  --description "SimpleOffice4Me self-hosted office and document management" \
  --url "https://github.com/JensKapitza/SimpleOffice4Me" \
  --license "GPL-3.0-or-later" \
  --maintainer "SimpleOffice4Me" \
  --category "office" \
  --depends "python3 (>= 3.10)" \
  --depends "python3-venv" \
  --depends "git" \
  --depends "ca-certificates" \
  --deb-recommends "poppler-utils" \
  --deb-recommends "tesseract-ocr" \
  --deb-recommends "imagemagick" \
  --deb-recommends "ghostscript" \
  --deb-recommends "ffmpeg" \
  --deb-recommends "clamav" \
  --deb-recommends "libreoffice" \
  --config-files "/etc/simpleoffice4me/simpleoffice.env" \
  --after-install "$ROOT/packaging/postinst.sh" \
  --before-remove "$ROOT/packaging/prerm.sh" \
  --after-remove "$ROOT/packaging/postrm.sh" \
  --package "$OUT_DIR/${PACKAGE_NAME}_${VERSION}-${ITERATION}_${ARCH}.deb" \
  -C "$STAGE" \
  .

printf '\nPaket erstellt:\n  %s\n' "$OUT_DIR/${PACKAGE_NAME}_${VERSION}-${ITERATION}_${ARCH}.deb"
printf 'Installation:\n  sudo apt install ./%s\n' "$(basename "$OUT_DIR/${PACKAGE_NAME}_${VERSION}-${ITERATION}_${ARCH}.deb")"
