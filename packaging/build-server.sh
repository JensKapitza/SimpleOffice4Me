#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
CONFIG="$ROOT/packaging/build-server.local.sh"

if [ ! -f "$CONFIG" ]; then
  cat >&2 <<'EOF'
Lokale Server-Buildkonfiguration fehlt:
  packaging/build-server.local.sh

Erstelle sie z.B. mit:
  cp packaging/build-server.local.sh.example packaging/build-server.local.sh

Die lokale Datei ist per .gitignore ausgeschlossen.
EOF
  exit 2
fi

# shellcheck disable=SC1090
. "$CONFIG"

: "${SIMPLEOFFICE_SERVER_PUBLIC_URL:?SIMPLEOFFICE_SERVER_PUBLIC_URL fehlt in build-server.local.sh}"
: "${SIMPLEOFFICE_SERVER_PEER_ID:=license-master}"

export SIMPLEOFFICE_BUILD_ROLE=server
export SIMPLEOFFICE_BUILD_LICENSE_MASTER_MODE=1
export SIMPLEOFFICE_BUILD_LICENSE_MASTER_URL="$SIMPLEOFFICE_SERVER_PUBLIC_URL"
export SIMPLEOFFICE_BUILD_LICENSE_MASTER_PEER_ID="$SIMPLEOFFICE_SERVER_PEER_ID"

exec "$ROOT/packaging/build-fpm.sh"
