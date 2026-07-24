#!/usr/bin/env bash
# Update only fast-forward changes so local work is never overwritten.
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Update abgebrochen: Es gibt lokale Änderungen. Bitte erst committen oder sichern." >&2
  exit 1
fi

git pull --ff-only
exec "$ROOT/start.sh" "$@"
