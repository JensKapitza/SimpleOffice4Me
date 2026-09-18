#!/usr/bin/env bash
# Compatibility entrypoint. Lifecycle and Python selection live in start.sh.
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec "$ROOT/start.sh" mini-services "$@"
