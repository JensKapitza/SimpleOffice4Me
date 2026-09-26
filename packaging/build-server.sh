#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
CONFIG="$ROOT/packaging/build-server.local.sh"

# Local configuration remains the normal manual-build path. CI and other
# automation may provide the same non-secret values through the environment.
if [ -f "$CONFIG" ]; then
  # shellcheck disable=SC1090
  . "$CONFIG"
elif [ -z "${SIMPLEOFFICE_SERVER_PUBLIC_URL:-}" ]; then
  cat >&2 <<'EOF'
Server-Buildkonfiguration fehlt.

Entweder:
  cp packaging/build-server.local.sh.example packaging/build-server.local.sh

oder fuer automatisierte Builds:
  export SIMPLEOFFICE_SERVER_PUBLIC_URL=https://office.example.invalid
  export SIMPLEOFFICE_SERVER_PEER_ID=license-master
EOF
  exit 2
fi

: "${SIMPLEOFFICE_SERVER_PUBLIC_URL:?SIMPLEOFFICE_SERVER_PUBLIC_URL fehlt}"
: "${SIMPLEOFFICE_SERVER_PEER_ID:=license-master}"

export SIMPLEOFFICE_BUILD_ROLE=server
export SIMPLEOFFICE_BUILD_LICENSE_MASTER_MODE=1
export SIMPLEOFFICE_BUILD_LICENSE_MASTER_URL="$SIMPLEOFFICE_SERVER_PUBLIC_URL"
export SIMPLEOFFICE_BUILD_LICENSE_MASTER_PEER_ID="$SIMPLEOFFICE_SERVER_PEER_ID"

exec "$ROOT/packaging/build-fpm.sh"
