#!/usr/bin/env bash
# macOS Finder starter. Git preserves the executable permission of this file.
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec "$ROOT/start.sh" "$@"
