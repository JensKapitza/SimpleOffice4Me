#!/bin/sh
set -eu

STATE_DIR=${SIMPLEOFFICE_STATE_DIR:-/var/lib/simpleoffice4me}
INSTANCE_DIR="$STATE_DIR/instance"
DATABASE_DIR="$STATE_DIR/database"
DOCUMENT_DIR=${SIMPLEOFFICE_DOCUMENT_ROOT:-$STATE_DIR/documents}
CONFIG="$INSTANCE_DIR/simpleoffice.json"
ROLE=${SIMPLEOFFICE_CONTAINER_ROLE:-web}

mkdir -p "$INSTANCE_DIR" "$DATABASE_DIR" "$DOCUMENT_DIR"

# Web and error-relay roles initialize the named-volume ownership and then drop
# to the unprivileged application account. The network role deliberately does
# not change ownership and receives only its explicit capabilities from Compose.
if { [ "$ROLE" = "web" ] || [ "$ROLE" = "error-relay" ]; } && [ "$(id -u)" = "0" ]; then
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
    if [ "$ROLE" = "web" ] || [ "$ROLE" = "error-relay" ]; then
        chown simpleoffice:simpleoffice "$CONFIG" 2>/dev/null || true
    fi
fi

if [ "$ROLE" = "error-relay" ]; then
    # A mounted Docker secret may be world-readable inside /run/secrets. Copy it
    # once into the private SimpleOffice state tree with strict permissions.
    # The source path itself is not persisted and the secret is never baked into
    # the image or repository.
    if [ -n "${SIMPLEOFFICE_GITHUB_ERROR_TOKEN_SOURCE:-}" ]; then
        if [ ! -r "$SIMPLEOFFICE_GITHUB_ERROR_TOKEN_SOURCE" ]; then
            echo "GitHub error token source is not readable" >&2
            exit 78
        fi
        install -m 0600 "$SIMPLEOFFICE_GITHUB_ERROR_TOKEN_SOURCE" "$INSTANCE_DIR/github-error-token"
        if [ "$(id -u)" = "0" ]; then
            chown simpleoffice:simpleoffice "$INSTANCE_DIR/github-error-token"
        fi
        export SIMPLEOFFICE_GITHUB_ERROR_TOKEN_FILE="$INSTANCE_DIR/github-error-token"
    fi
    export SIMPLEOFFICE_ERROR_RELAY_ENABLED=${SIMPLEOFFICE_ERROR_RELAY_ENABLED:-1}
    export SIMPLEOFFICE_GITHUB_ERROR_REPORTING=${SIMPLEOFFICE_GITHUB_ERROR_REPORTING:-1}
    export SIMPLEOFFICE_BACKGROUND_INDEX=0
    export SIMPLEOFFICE_OSM_INDEX=0
    export SIMPLEOFFICE_DATALOGGER=0
    export SIMPLEOFFICE_MCP=0
fi

case "$ROLE" in
    web|error-relay)
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
