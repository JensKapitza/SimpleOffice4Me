#!/bin/sh
set -eu

STATE_DIR=${SIMPLEOFFICE_STATE_DIR:-/var/lib/simpleoffice4me}
INSTANCE_DIR="$STATE_DIR/instance"
DATABASE_DIR="$STATE_DIR/database"
DOCUMENT_DIR=${SIMPLEOFFICE_DOCUMENT_ROOT:-$STATE_DIR/documents}
CONFIG="$INSTANCE_DIR/simpleoffice.json"
ROLE=${SIMPLEOFFICE_CONTAINER_ROLE:-web}

mkdir -p "$INSTANCE_DIR" "$DATABASE_DIR" "$DOCUMENT_DIR"

# The web role initializes named-volume ownership and then drops to the
# unprivileged application account. The network role deliberately does not
# change ownership and receives only its explicit capabilities from Compose.
if [ "$ROLE" = "web" ] && [ "$(id -u)" = "0" ]; then
    chown -R simpleoffice:simpleoffice "$STATE_DIR"
fi

if [ ! -f "$CONFIG" ]; then
    HOST=${SIMPLEOFFICE_HOST:-0.0.0.0}
    PORT=${SIMPLEOFFICE_PORT:-8080}
    umask 077
    cat >"$CONFIG" <<EOF
{
  "version": 1,
  "document_root": "$DOCUMENT_DIR",
  "host": "$HOST",
  "port": $PORT
}
EOF
    if [ "$ROLE" = "web" ]; then
        chown simpleoffice:simpleoffice "$CONFIG" 2>/dev/null || true
    fi
fi

case "$ROLE" in
    web)
        export SIMPLEOFFICE_HOST=${SIMPLEOFFICE_HOST:-0.0.0.0}
        if [ "$(id -u)" = "0" ]; then
            exec gosu simpleoffice "$@"
        fi
        exec "$@"
        ;;
    mini-services)
        # The process remains uid 0 only inside this capability-restricted
        # container. It is not Docker-privileged and cannot gain capabilities
        # beyond those listed in compose.lan.yaml.
        if [ "$(id -u)" != "0" ]; then
            echo "mini-services role must start as root with restricted container capabilities" >&2
            exit 70
        fi
        exec "$@"
        ;;
    *)
        echo "unknown SIMPLEOFFICE_CONTAINER_ROLE: $ROLE" >&2
        exit 64
        ;;
esac
