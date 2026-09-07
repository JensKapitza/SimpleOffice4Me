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
LICENSE_MASTER_URL="${SIMPLEOFFICE_BUILD_LICENSE_MASTER_URL:-}"
LICENSE_MASTER_PEER_ID="${SIMPLEOFFICE_BUILD_LICENSE_MASTER_PEER_ID:-license-master}"
LICENSE_MASTER_MODE="${SIMPLEOFFICE_BUILD_LICENSE_MASTER_MODE:-0}"

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

python3 - "$LICENSE_MASTER_URL" "$LICENSE_MASTER_PEER_ID" "$LICENSE_MASTER_MODE" <<'PY'
import re
import sys
from urllib.parse import urlsplit
url, peer_id, mode = sys.argv[1:]
if url:
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise SystemExit('SIMPLEOFFICE_BUILD_LICENSE_MASTER_URL muss eine HTTP(S)-Basis-URL ohne Credentials sein')
    if parsed.query or parsed.fragment:
        raise SystemExit('Lizenz-Master-URL darf keine Query oder Fragmente enthalten')
if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', peer_id):
    raise SystemExit('Ungültige SIMPLEOFFICE_BUILD_LICENSE_MASTER_PEER_ID')
if mode.strip().lower() not in {'0', '1', 'false', 'true', 'no', 'yes', 'off', 'on'}:
    raise SystemExit('SIMPLEOFFICE_BUILD_LICENSE_MASTER_MODE muss 0/1 bzw. true/false sein')
PY

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

# Freeze the license master into the package staging tree. There is deliberately
# no runtime setting for this URL. Changing it requires a new build. A source
# checkout can choose its own master by setting the build variables above.
python3 - "$APP_DIR/app/build_master.py" "$LICENSE_MASTER_URL" "$LICENSE_MASTER_PEER_ID" "$LICENSE_MASTER_MODE" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
url, peer_id, mode = sys.argv[2:]
is_master = mode.strip().lower() in {'1', 'true', 'yes', 'on'}
path.write_text(
    '"""Generated at package build time; do not edit on installed systems."""\n'
    f'LICENSE_MASTER_URL = {url!r}\n'
    f'LICENSE_MASTER_PEER_ID = {peer_id!r}\n'
    f'LICENSE_MASTER_MODE = {is_master!r}\n',
    encoding='utf-8',
)
PY

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
printf 'Lizenz-Master:\n  %s\n' "${LICENSE_MASTER_URL:-nicht eingebettet}"
printf 'Master-Build:\n  %s\n' "$LICENSE_MASTER_MODE"
