#!/bin/sh
set -eu

APP_DIR=/opt/simpleoffice4me
STATE_DIR=/var/lib/simpleoffice4me
INSTANCE_DIR="$STATE_DIR/instance"
DOCUMENT_DIR="$STATE_DIR/documents"
VENV="$APP_DIR/.venv"

if ! getent group simpleoffice >/dev/null 2>&1; then
    addgroup --system simpleoffice >/dev/null 2>&1 || groupadd --system simpleoffice
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

"$VENV/bin/python" -m pip install \
    --no-index \
    --find-links "$APP_DIR/wheelhouse" \
    --disable-pip-version-check \
    --upgrade \
    simpleoffice4me

chown -R root:root "$APP_DIR"
chown -R simpleoffice:simpleoffice "$STATE_DIR"

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
fi

cat <<'EOF'
SimpleOffice4Me wurde installiert.
Start:   sudo systemctl start simpleoffice4me
Status:  systemctl status simpleoffice4me
Log:     journalctl -u simpleoffice4me -f
Direkt:  simpleoffice4me
Konfig:  /etc/simpleoffice4me/simpleoffice.env
Daten:   /var/lib/simpleoffice4me
EOF
