#!/bin/sh
set -eu

STATE_DIR=${SIMPLEOFFICE_STATE_DIR:-/var/lib/simpleoffice4me}
INSTANCE_DIR="$STATE_DIR/instance"
DATABASE_DIR="$STATE_DIR/database"
DOCUMENT_DIR=${SIMPLEOFFICE_DOCUMENT_ROOT:-$STATE_DIR/documents}
CONFIG="$INSTANCE_DIR/simpleoffice.json"
ROLE=${SIMPLEOFFICE_CONTAINER_ROLE:-web}

mkdir -p "$INSTANCE_DIR" "$DATABASE_DIR" "$DOCUMENT_DIR"

# Named volumes are created as root. Only the dedicated network worker remains
# root inside its capability-restricted container; the normal web process drops
# privileges before importing the Flask application.
if [ "$(id -u)" = "0" ]; then
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
    chown simpleoffice:simpleoffice "$CONFIG" 2>/dev/null || true
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
        # Docker Compose grants only NET_BIND_SERVICE, NET_RAW and NET_ADMIN to
        # this container. Keeping uid 0 here avoids relying on ambient-capability
        # propagation through an extra privilege-drop helper.
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
