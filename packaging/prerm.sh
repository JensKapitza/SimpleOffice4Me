#!/bin/sh
set -eu

if command -v systemctl >/dev/null 2>&1; then
    systemctl stop simpleoffice4me.service >/dev/null 2>&1 || true
    systemctl stop simpleoffice-mini-services.service >/dev/null 2>&1 || true
    if [ -x /opt/simpleoffice4me/.venv/bin/python ]; then
        if ! PYTHONPATH=/opt/simpleoffice4me /opt/simpleoffice4me/.venv/bin/python -m simpleoffice_firewall_agent --rollback-pending >/dev/null 2>&1; then
            echo "Offene Firewall-Tests konnten nicht sicher zurückgerollt werden; Paketentfernung wird abgebrochen." >&2
            exit 1
        fi
    fi
    systemctl stop simpleoffice-firewall-agent.service >/dev/null 2>&1 || true
    systemctl stop simpleoffice-firewall-agent.socket >/dev/null 2>&1 || true
    if [ "${1:-}" = "remove" ] || [ "${1:-}" = "purge" ]; then
        systemctl disable simpleoffice4me.service >/dev/null 2>&1 || true
        systemctl disable simpleoffice-mini-services.service >/dev/null 2>&1 || true
        systemctl disable simpleoffice-firewall-agent.socket >/dev/null 2>&1 || true
    fi
fi
