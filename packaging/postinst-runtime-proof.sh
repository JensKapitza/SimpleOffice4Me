#!/bin/sh
set -eu

APP_DIR=/opt/simpleoffice4me
PYTHON=/usr/bin/python3.10
PROOF_DIR=/var/cache/simpleoffice4me/runtime-proof

[ -x "$PYTHON" ] || exit 0
[ -f "$APP_DIR/simpleoffice_runtime_support.py" ] || exit 0
[ -r /etc/os-release ] || exit 0
if ! ( . /etc/os-release; [ "${ID:-}" = ubuntu ] && [ "${VERSION_ID:-}" = 22.04 ); then
    exit 0
fi
install -d -o root -g root -m 0755 "$PROOF_DIR"
SIMPLEOFFICE_RUNTIME_PROOF_DIR="$PROOF_DIR" "$PYTHON" "$APP_DIR/simpleoffice_runtime_support.py" --write-proof
