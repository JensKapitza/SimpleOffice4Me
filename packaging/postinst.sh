#!/bin/sh
set -eu

APP_DIR=/opt/simpleoffice4me
if [ "${1:-}" = "triggered" ]; then
    exec sh "$APP_DIR/packaging/postinst-runtime-proof.sh"
fi
STATE_DIR=/var/lib/simpleoffice4me
INSTANCE_DIR="$STATE_DIR/instance"
DOCUMENT_DIR="$STATE_DIR/documents"
VENV="$APP_DIR/.venv"
RUNTIME_PROOF_DIR=/var/cache/simpleoffice4me/runtime-proof

# Bootstrap the authenticated local proof before the normal non-network gate
# when this host uses the supported Ubuntu 22.04 system Python 3.10 exception.
if [ -x /usr/bin/python3.10 ] && [ "$(readlink -f "$(command -v python3)")" = /usr/bin/python3.10 ]; then
    install -d -o root -g root -m 0755 "$RUNTIME_PROOF_DIR"
    SIMPLEOFFICE_RUNTIME_PROOF_DIR="$RUNTIME_PROOF_DIR" /usr/bin/python3.10 "$APP_DIR/simpleoffice_runtime_support.py" --write-proof
fi

python3 "$APP_DIR/simpleoffice_runtime_support.py"

if ! getent group simpleoffice >/dev/null 2>&1; then
    addgroup --system simpleoffice >/dev/null 2>&1 || groupadd --system simpleoffice
fi
if ! getent group simpleoffice-firewall >/dev/null 2>&1; then
    addgroup --system simpleoffice-firewall >/dev/null 2>&1 || groupadd --system simpleoffice-firewall
fi
if ! id simpleoffice >/dev/null 2>&1; then
    adduser --system --ingroup simpleoffice --home "$STATE_DIR" --no-create-home --shell /usr/sbin/nologin simpleoffice >/dev/null 2>&1 \
      || useradd --system --gid simpleoffice --home-dir "$STATE_DIR" --shell /usr/sbin/nologin simpleoffice
fi

install -d -o simpleoffice -g simpleoffice -m 0750 "$STATE_DIR" "$INSTANCE_DIR" "$DOCUMENT_DIR"
install -d -o root -g simpleoffice -m 0750 /etc/simpleoffice4me

rm -rf "$APP_DIR/instance"
ln -s "$INSTANCE_DIR" "$APP_DIR/instance"

if [ ! -x "$VENV/bin/python" ]; then
    python3 -m venv "$VENV"
fi

"$VENV/bin/python" "$APP_DIR/simpleoffice_runtime_support.py"

INSTALL_SPEC=simpleoffice4me
EXTRAS_FILE="$APP_DIR/.install-extras"
if [ -f "$EXTRAS_FILE" ]; then
    INSTALL_EXTRAS="$(tr -d '[:space:]' < "$EXTRAS_FILE")"
    case "$INSTALL_EXTRAS" in
        *[!A-Za-z0-9_,.-]*)
            echo "Ungueltige Python-Extras im Paket: $INSTALL_EXTRAS" >&2
            exit 1
            ;;
    esac
    if [ -n "$INSTALL_EXTRAS" ]; then
        INSTALL_SPEC="simpleoffice4me[$INSTALL_EXTRAS]"
    fi
fi

"$VENV/bin/python" -m pip install \
    --no-index \
    --find-links "$APP_DIR/wheelhouse" \
    --disable-pip-version-check \
    --upgrade \
    "$INSTALL_SPEC"

chown -R root:root "$APP_DIR"
chown -R simpleoffice:simpleoffice "$STATE_DIR"
FIREWALL_STATE_DIR=/var/lib/simpleoffice4me-firewall-agent
install -d -o root -g root -m 0755 "$RUNTIME_PROOF_DIR"
if [ -x /usr/bin/python3.10 ] && [ "$(readlink -f "$VENV/bin/python")" = /usr/bin/python3.10 ]; then
    SIMPLEOFFICE_RUNTIME_PROOF_DIR="$RUNTIME_PROOF_DIR" "$VENV/bin/python" "$APP_DIR/simpleoffice_runtime_support.py" --write-proof
fi
if [ -L "$FIREWALL_STATE_DIR" ]; then
    echo "Unsicherer Firewall-Agent-State: $FIREWALL_STATE_DIR ist ein Symlink." >&2
    exit 1
fi
install -d -o root -g root -m 0700 "$FIREWALL_STATE_DIR" "$FIREWALL_STATE_DIR/tests"
chown -hR root:root "$FIREWALL_STATE_DIR"
find "$FIREWALL_STATE_DIR" -xdev -type d -exec chmod 0700 {} +
find "$FIREWALL_STATE_DIR" -xdev -type f -exec chmod 0600 {} +

CONFIG="$INSTANCE_DIR/simpleoffice.json"
if [ ! -f "$CONFIG" ]; then
    cat >"$CONFIG" <<EOF
{
  "version": 1,
  "document_root": "$DOCUMENT_DIR",
  "host": "127.0.0.1",
  "port": 8080
}
EOF
    chown simpleoffice:simpleoffice "$CONFIG"
    chmod 0600 "$CONFIG"
fi

if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload || true
    systemctl enable simpleoffice4me.service >/dev/null 2>&1 || true
    systemctl enable simpleoffice-mini-services.service >/dev/null 2>&1 || true
    systemctl enable --now simpleoffice-firewall-agent.socket >/dev/null 2>&1 || true
    # The worker itself is safe to run immediately because DHCP and DNS are
    # disabled in the default configuration. It then watches the admin-managed
    # config file and activates services without granting sudo to the web app.
    systemctl restart simpleoffice-mini-services.service >/dev/null 2>&1 || true
fi

printf '\nMaster-Identitaet des installierten Pakets:\n'
(
    cd "$APP_DIR"
    "$VENV/bin/python" -m tools.launcher master-status
)

cat <<'EOF'
SimpleOffice4Me wurde installiert.
Start:   sudo systemctl start simpleoffice4me
Status:  systemctl status simpleoffice4me
Log:     journalctl -u simpleoffice4me -f
Mini:    systemctl status simpleoffice-mini-services
MiniLog: journalctl -u simpleoffice-mini-services -f
Firewall: systemctl status simpleoffice-firewall-agent.socket
Direkt:  simpleoffice4me
Konfig:  /etc/simpleoffice4me/simpleoffice.env
Daten:   /var/lib/simpleoffice4me
EOF
