#!/bin/sh
set -eu

if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload >/dev/null 2>&1 || true
fi

# Never delete user documents or instance state automatically. Even a purge
# leaves /var/lib/simpleoffice4me intact so an accidental package removal does
# not become a data-loss event.
if [ "${1:-}" = "purge" ] && [ -d /var/lib/simpleoffice4me ]; then
    echo "SimpleOffice4Me-Daten bleiben erhalten: /var/lib/simpleoffice4me" >&2
fi
