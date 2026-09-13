#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${1:-$PWD/simpleoffice-backups}"
mkdir -p "$BACKUP_DIR"
BACKUP_DIR="$(cd "$BACKUP_DIR" && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="simpleoffice4me-data-$STAMP.tar.gz"

SUDO=""
if ! docker info >/dev/null 2>&1; then
    if command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
        SUDO="sudo"
    else
        echo "Docker-Daemon ist nicht erreichbar." >&2
        exit 1
    fi
fi

$SUDO docker run --rm \
    --entrypoint /bin/tar \
    -v simpleoffice4me-data:/data:ro \
    -v "$BACKUP_DIR:/backup" \
    ghcr.io/jenskapitza/simpleoffice4me:latest \
    -czf "/backup/$ARCHIVE" -C /data .

printf 'Backup erstellt: %s/%s\n' "$BACKUP_DIR" "$ARCHIVE"
