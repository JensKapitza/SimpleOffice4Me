#!/usr/bin/env bash
set -euo pipefail

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
COMPOSE_FILE="$HERE/compose.yaml"

SUDO=""
if ! docker info >/dev/null 2>&1; then
    if command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
        SUDO="sudo"
    else
        echo "Docker-Daemon ist nicht erreichbar." >&2
        exit 1
    fi
fi

DOCKER=($SUDO docker)
if "${DOCKER[@]}" compose version >/dev/null 2>&1; then
    COMPOSE=("${DOCKER[@]}" compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=($SUDO docker-compose)
else
    echo "Docker Compose fehlt." >&2
    exit 1
fi

"${COMPOSE[@]}" -f "$COMPOSE_FILE" pull
"${COMPOSE[@]}" -f "$COMPOSE_FILE" up -d --remove-orphans
"${COMPOSE[@]}" -f "$COMPOSE_FILE" ps

printf '\nUpdate abgeschlossen. Das Volume simpleoffice4me-data wurde nicht geloescht.\n'
