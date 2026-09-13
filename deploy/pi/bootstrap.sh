#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${SIMPLEOFFICE_INSTALL_DIR:-/opt/simpleoffice4me}"
REF="${SIMPLEOFFICE_INSTALL_REF:-main}"
BASE_URL="https://raw.githubusercontent.com/JensKapitza/SimpleOffice4Me/${REF}/deploy/pi"

if [ "$(id -u)" -ne 0 ]; then
    echo "Bitte als root ausfuehren, z. B. curl ... | sudo bash" >&2
    exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update
        apt-get install -y ca-certificates curl
    else
        echo "curl fehlt und kann auf diesem System nicht automatisch installiert werden." >&2
        exit 1
    fi
fi

install -d -m 0755 "$INSTALL_DIR"
for file in compose.yaml install.sh update.sh backup.sh; do
    curl --fail --silent --show-error --location \
        "$BASE_URL/$file" \
        --output "$INSTALL_DIR/$file"
done
chmod 0755 "$INSTALL_DIR/install.sh" "$INSTALL_DIR/update.sh" "$INSTALL_DIR/backup.sh"
chmod 0644 "$INSTALL_DIR/compose.yaml"

exec "$INSTALL_DIR/install.sh"
