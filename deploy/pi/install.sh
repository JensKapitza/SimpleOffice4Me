#!/usr/bin/env bash
set -euo pipefail

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
COMPOSE_FILE="$HERE/compose.yaml"

log() {
    printf '\n==> %s\n' "$*"
}

need_cmd() {
    command -v "$1" >/dev/null 2>&1
}

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    if need_cmd sudo; then
        SUDO="sudo"
    else
        echo "Fehler: Root-Rechte oder sudo werden benoetigt." >&2
        exit 1
    fi
fi

install_docker_debian() {
    if ! need_cmd apt-get; then
        echo "Docker fehlt. Bitte Docker Engine und Docker Compose installieren und das Skript erneut starten." >&2
        exit 1
    fi

    log "Docker wird aus den Distribution-Paketen installiert"
    $SUDO apt-get update
    if ! $SUDO apt-get install -y docker.io docker-compose-plugin; then
        $SUDO apt-get install -y docker.io docker-compose
    fi
    $SUDO systemctl enable --now docker
}

if ! need_cmd docker; then
    install_docker_debian
fi

DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
    if [ -n "$SUDO" ] && $SUDO docker info >/dev/null 2>&1; then
        DOCKER=($SUDO docker)
    else
        echo "Fehler: Docker ist installiert, aber der Daemon ist nicht erreichbar." >&2
        exit 1
    fi
fi

if "${DOCKER[@]}" compose version >/dev/null 2>&1; then
    COMPOSE=("${DOCKER[@]}" compose)
elif need_cmd docker-compose; then
    if docker-compose version >/dev/null 2>&1; then
        COMPOSE=(docker-compose)
    elif [ -n "$SUDO" ]; then
        COMPOSE=($SUDO docker-compose)
    else
        echo "Fehler: Docker Compose ist nicht nutzbar." >&2
        exit 1
    fi
else
    echo "Fehler: Docker Compose fehlt." >&2
    exit 1
fi

ARCH="$(uname -m)"
case "$ARCH" in
    aarch64|arm64|x86_64|amd64)
        ;;
    *)
        echo "Fehler: Architektur $ARCH wird vom vorkompilierten Image nicht unterstuetzt." >&2
        echo "Unterstuetzt: arm64/aarch64 und amd64/x86_64." >&2
        exit 1
        ;;
esac

log "SimpleOffice4Me Image wird geladen"
"${COMPOSE[@]}" -f "$COMPOSE_FILE" pull

log "SimpleOffice4Me wird gestartet"
"${COMPOSE[@]}" -f "$COMPOSE_FILE" up -d --remove-orphans

log "Containerstatus"
"${COMPOSE[@]}" -f "$COMPOSE_FILE" ps

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
HOST="${IP:-$(hostname)}"
PORT="${SIMPLEOFFICE_HTTP_PORT:-8080}"

printf '\nSimpleOffice4Me ist installiert.\n'
printf 'Aufruf im LAN: http://%s:%s\n' "$HOST" "$PORT"
printf 'Daten bleiben im Docker-Volume simpleoffice4me-data erhalten.\n'
printf 'Update: %s/update.sh\n' "$HERE"
printf 'Logs: docker logs -f simpleoffice4me\n'
