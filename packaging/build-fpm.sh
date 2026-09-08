#!/usr/bin/env bash
# Internal backend for SimpleOffice4Me package builds.
# Use build-client.sh or build-server.sh instead of invoking this file directly.
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

BUILD_ROLE="${SIMPLEOFFICE_BUILD_ROLE:-}"
case "$BUILD_ROLE" in
  client|server) ;;
  *)
    cat >&2 <<'EOF'
Kein gueltiger Build-Typ gesetzt.
Bitte nicht packaging/build-fpm.sh direkt aufrufen.

Client bauen:
  ./packaging/build-client.sh

Lizenz-Master/Server bauen:
  ./packaging/build-server.sh
EOF
    exit 2
    ;;
esac

PACKAGE_NAME="${SIMPLEOFFICE_PACKAGE_NAME:-simpleoffice4me-${BUILD_ROLE}}"
VERSION="${SIMPLEOFFICE_PACKAGE_VERSION:-$(python3 - <<'PY'
import tomllib
from pathlib import Path
print(tomllib.loads(Path('pyproject.toml').read_text(encoding='utf-8'))['project']['version'])
PY
)}"
ITERATION="${SIMPLEOFFICE_PACKAGE_ITERATION:-1}"
ARCH="${SIMPLEOFFICE_PACKAGE_ARCH:-$(dpkg --print-architecture 2>/dev/null || printf 'all')}"
OUT_DIR="${SIMPLEOFFICE_PACKAGE_OUT:-$ROOT/dist/packages}"
WORK_DIR="${SIMPLEOFFICE_PACKAGE_WORK:-$ROOT/build/fpm-${BUILD_ROLE}}"
STAGE="$WORK_DIR/root"
APP_DIR="$STAGE/opt/simpleoffice4me"
WHEELHOUSE="$APP_DIR/wheelhouse"
LICENSE_MASTER_URL="${SIMPLEOFFICE_BUILD_LICENSE_MASTER_URL:-}"
LICENSE_MASTER_PEER_ID="${SIMPLEOFFICE_BUILD_LICENSE_MASTER_PEER_ID:-license-master}"
LICENSE_MASTER_MODE="${SIMPLEOFFICE_BUILD_LICENSE_MASTER_MODE:-0}"

if [ "$BUILD_ROLE" = "server" ]; then
  case "${LICENSE_MASTER_MODE,,}" in
    1|true|yes|on) ;;
    *) echo "Server-Build muss als Lizenz-Master gebaut werden." >&2; exit 2 ;;
  esac
else
  case "${LICENSE_MASTER_MODE,,}" in
    0|false|no|off) ;;
    *) echo "Client-Build darf nicht im Lizenz-Master-Modus laufen." >&2; exit 2 ;;
  esac
fi

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
if not url:
    raise SystemExit('Lizenz-Master-URL fehlt fuer diesen Build')
parsed = urlsplit(url)
if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
    raise SystemExit('Lizenz-Master-URL muss eine HTTP(S)-Basis-URL ohne Credentials sein')
if parsed.query or parsed.fragment:
    raise SystemExit('Lizenz-Master-URL darf keine Query oder Fragmente enthalten')
if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', peer_id):
    raise SystemExit('Ungueltige Lizenz-Master-Peer-ID')
if mode.strip().lower() not in {'0', '1', 'false', 'true', 'no', 'yes', 'off', 'on'}:
    raise SystemExit('Ungueltiger Lizenz-Master-Modus')
PY

rm -rf "$WORK_DIR"
mkdir -p "$APP_DIR" "$WHEELHOUSE" "$OUT_DIR"

# Copy only versioned application sources. Local build configuration and all
# secret/private build material are explicitly excluded even if someone places
# such files below packaging/ by mistake.
tar -C "$ROOT" -cf - \
  --exclude='./.git' \
  --exclude='./.venv' \
  --exclude='./instance' \
  --exclude='./build' \
  --exclude='./dist' \
  --exclude='./var' \
  --exclude='./database/*.sqlite' \
  --exclude='./database/*.sqlite-*' \
  --exclude='./packaging/build-client.local.sh' \
  --exclude='./packaging/build-server.local.sh' \
  --exclude='./packaging/*.secret.sh' \
  --exclude='./packaging/private' \
  --exclude='./packaging/private/*' \
  --exclude='./__pycache__' \
  --exclude='*/__pycache__' \
  . | tar -C "$APP_DIR" -xf -

# Freeze only non-secret master identity into the package. Authentication
# secrets are runtime configuration and are never embedded into package source.
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

python3 -m pip wheel \
  --wheel-dir "$WHEELHOUSE" \
  --disable-pip-version-check \
  "$ROOT"

install -D -m 0755 "$ROOT/packaging/simpleoffice4me-wrapper.sh" "$STAGE/usr/bin/simpleoffice4me"
install -D -m 0644 "$ROOT/packaging/simpleoffice4me.service" "$STAGE/lib/systemd/system/simpleoffice4me.service"
install -D -m 0644 "$ROOT/packaging/simpleoffice-mini-services.service" "$STAGE/lib/systemd/system/simpleoffice-mini-services.service"
install -D -m 0644 "$ROOT/packaging/simpleoffice.env" "$STAGE/etc/simpleoffice4me/simpleoffice.env"
install -D -m 0644 "$ROOT/packaging/README-system-package.md" "$STAGE/usr/share/doc/simpleoffice4me/README.system-package.md"

rm -rf "$APP_DIR/instance"

fpm \
  -s dir \
  -t deb \
  -n "$PACKAGE_NAME" \
  -v "$VERSION" \
  --iteration "$ITERATION" \
  --architecture "$ARCH" \
  --description "SimpleOffice4Me self-hosted office and document management (${BUILD_ROLE})" \
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
printf 'Build-Typ:\n  %s\n' "$BUILD_ROLE"
printf 'Lizenz-Master:\n  %s\n' "$LICENSE_MASTER_URL"
printf 'Master-Build:\n  %s\n' "$LICENSE_MASTER_MODE"
printf '\nHinweis: Tokens/Passwoerter wurden nicht in das Paket eingebettet.\n'
