#!/bin/sh
set -eu

if command -v systemctl >/dev/null 2>&1; then
    systemctl stop simpleoffice4me.service >/dev/null 2>&1 || true
    if [ "${1:-}" = "remove" ] || [ "${1:-}" = "purge" ]; then
        systemctl disable simpleoffice4me.service >/dev/null 2>&1 || true
    fi
fi
