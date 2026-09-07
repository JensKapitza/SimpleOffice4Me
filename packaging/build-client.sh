#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
CONFIG="$ROOT/packaging/build-client.local.sh"

if [ ! -f "$CONFIG" ]; then
  cat >&2 <<'EOF'
Lokale Client-Buildkonfiguration fehlt:
  packaging/build-client.local.sh

Erstelle sie z.B. mit:
  cp packaging/build-client.local.sh.example packaging/build-client.local.sh

Die lokale Datei ist per .gitignore ausgeschlossen.
EOF
  exit 2
fi

# shellcheck disable=SC1090
. "$CONFIG"

: "${SIMPLEOFFICE_CLIENT_MASTER_URL:?SIMPLEOFFICE_CLIENT_MASTER_URL fehlt in build-client.local.sh}"
: "${SIMPLEOFFICE_CLIENT_MASTER_PEER_ID:=license-master}"

export SIMPLEOFFICE_BUILD_ROLE=client
export SIMPLEOFFICE_BUILD_LICENSE_MASTER_MODE=0
export SIMPLEOFFICE_BUILD_LICENSE_MASTER_URL="$SIMPLEOFFICE_CLIENT_MASTER_URL"
export SIMPLEOFFICE_BUILD_LICENSE_MASTER_PEER_ID="$SIMPLEOFFICE_CLIENT_MASTER_PEER_ID"

exec "$ROOT/packaging/build-fpm.sh"
