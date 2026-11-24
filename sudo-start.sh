#!/usr/bin/env bash
# Start SimpleOffice4Me with privilege escalation when privileged ports
# (<1024, e.g. 80/443) are requested, plus firewall helpers.
#
# Escalation is least-privilege: without a privileged port the server starts
# directly. Otherwise sudo is preferred (non-interactive check, then group
# membership); su is the fallback. All configuration travels as start.sh
# arguments, never as inherited environment across the privilege boundary.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
START_SH="$ROOT/start.sh"

usage() {
  cat << 'EOF'
Verwendung: ./sudo-start.sh [OPTIONEN] [-- START.SH-OPTIONEN...]

Startet ./start.sh, bei privilegierten Ports (<1024) mit Root-Rechten.
Alle unbekannten Optionen werden unverändert an start.sh weitergereicht.

Eigene Optionen:
  --port PORT         HTTP-Port 1-65535 (Standard: 8080 bzw. Ersteinrichtung)
  --proxy-port PORT   LAN-Proxy-Port 1024-65535 (nur für --open-firewall)
  --open-firewall     Ports in ufw/firewalld freischalten (nur mit --port/--proxy-port)
  --print-command     Befehle nur anzeigen, nichts starten
  --help              Diese Hilfe anzeigen

Beispiele:
  ./sudo-start.sh --global --port 80 --proxy-public-host office.meinnetz.de --open-firewall
  ./sudo-start.sh --port 443 --proxy-port 8443 --print-command

Alle weiteren Optionen (z. B. --global, --proxy-bind, --proxy-public-host)
werden an start.sh weitergereicht; siehe ./start.sh --help.

Hinweise: Ports unter 1024 brauchen Root (sudo, sonst su). Die Eskalation
fragt ggf. nach einem Passwort. Umgebungsvariablen werden nicht über die
Root-Grenze übernommen – Konfiguration bitte als Argumente übergeben.
Laufzeitdateien können danach root gehören (ggf. chown nötig).
EOF
}

fail() { echo "sudo-start.sh: $1" >&2; exit "${2:-2}"; }

is_number() {
  case "$1" in *[!0-9]*|'') return 1 ;; esac
}

MAIN_PORT=""
PROXY_PORT=""
OPEN_FIREWALL=0
PRINT_COMMAND=0
FORWARD_ARGS=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --open-firewall) OPEN_FIREWALL=1; shift ;;
    --print-command) PRINT_COMMAND=1; shift ;;
    --port|--proxy-port)
      [ "$#" -ge 2 ] || fail "$1 benötigt eine Zahl."
      is_number "$2" || fail "Port muss eine ganze Zahl sein: $2"
      if [ "$1" = "--port" ]; then
        { [ "$2" -ge 1 ] && [ "$2" -le 65535 ]; } || fail "Port muss zwischen 1 und 65535 liegen."
        MAIN_PORT="$2"
      else
        { [ "$2" -ge 1024 ] && [ "$2" -le 65535 ]; } || fail "Proxy-Port muss zwischen 1024 und 65535 liegen."
        PROXY_PORT="$2"
      fi
      FORWARD_ARGS+=("$1" "$2"); shift 2 ;;
    --) shift; while [ "$#" -gt 0 ]; do FORWARD_ARGS+=("$1"); shift; done ;;
    *) FORWARD_ARGS+=("$1"); shift ;;
  esac
done

[ -x "$START_SH" ] || fail "start.sh fehlt oder ist nicht ausführbar: $START_SH"

needs_root() {
  if [ -n "$MAIN_PORT" ] && [ "$MAIN_PORT" -lt 1024 ]; then
    return 0
  fi
  [ "$OPEN_FIREWALL" -eq 1 ] && [ "$(firewall_backend)" != "manual" ]
}

# Echoes: none | sudo | su. SUDO_START_FORCE overrides for tests and
# wins over uid auto-detection so dry-runs stay deterministic as root.
select_escalation() {
  if [ -n "${SUDO_START_FORCE:-}" ]; then
    case "$SUDO_START_FORCE" in
      none|sudo|su) echo "$SUDO_START_FORCE"; return 0 ;;
      *) fail "SUDO_START_FORCE muss none, sudo oder su sein." ;;
    esac
  fi
  if [ "$(id -u)" -eq 0 ]; then
    echo "none"; return 0
  fi
  needs_root || { echo "none"; return 0; }
  if command -v sudo >/dev/null 2>&1 && {
       sudo -n true >/dev/null 2>&1 || id -nG 2>/dev/null | tr ' ' '\n' | grep -qxE 'sudo|wheel|admin'
     }; then
    echo "sudo"; return 0
  fi
  if command -v su >/dev/null 2>&1; then
    echo "su"; return 0
  fi
  fail "Privilegierter Port $MAIN_PORT braucht Root, aber weder sudo noch su ist verfügbar."
}

firewall_backend() {
  if command -v ufw >/dev/null 2>&1; then echo "ufw"
  elif command -v firewall-cmd >/dev/null 2>&1; then echo "firewalld"
  else echo "manual"
  fi
}

# Run "$@" privileged when escalation is active; print it for --print-command.
fw_run() {
  case "$MODE/$PRINT_COMMAND" in
    */1)
      case "$MODE" in
        none) printf '+ ' ;;
        sudo) printf '+ sudo -- ' ;;
        su) printf '+ su -p root -c %q\n' "exec $(printf '%q ' "$@")"; return 0 ;;
      esac
      printf '%q ' "$@"
      printf '\n'
      return 0
      ;;
    none/0) "$@" ;;
    sudo/0) sudo -- "$@" ;;
    su/0) su -p root -c "exec $(printf '%q ' "$@")" ;;
  esac
}

open_firewall() {
  [ -n "$MAIN_PORT$PROXY_PORT" ] || fail "--open-firewall braucht --port und/oder --proxy-port."
  local backend ports port
  backend="$(firewall_backend)"
  ports="$MAIN_PORT"
  [ -n "$PROXY_PORT" ] && ports="$ports $PROXY_PORT"
  if [ "$backend" = "manual" ]; then
    echo "Kein ufw/firewalld gefunden – Ports manuell freischalten, z. B.:" >&2
    for port in $ports; do
      echo "  iptables -C INPUT -p tcp --dport $port -j ACCEPT 2>/dev/null || iptables -A INPUT -p tcp --dport $port -j ACCEPT" >&2
    done
    return 0
  fi
  for port in $ports; do
    if [ "$backend" = "ufw" ]; then
      fw_run ufw allow "$port/tcp" || fail "ufw konnte Port $port nicht freischalten."
    else
      fw_run firewall-cmd --permanent --add-port="$port/tcp" \
        || fail "firewalld konnte Port $port nicht freischalten."
    fi
  done
  if [ "$backend" = "firewalld" ]; then
    fw_run firewall-cmd --reload || fail "firewalld-Neuladen ist fehlgeschlagen."
  fi
}

MODE="$(select_escalation)"

if [ "$OPEN_FIREWALL" -eq 1 ]; then
  open_firewall || exit $?
fi

case "$MODE" in
  none)
    if [ "$PRINT_COMMAND" -eq 1 ]; then
      printf '%q ' "$START_SH" "${FORWARD_ARGS[@]}"; printf '\n'
    else
      exec "$START_SH" "${FORWARD_ARGS[@]}"
    fi
    ;;
  sudo)
    if [ "$PRINT_COMMAND" -eq 1 ]; then
      printf 'sudo -- '; printf '%q ' "$START_SH" "${FORWARD_ARGS[@]}"; printf '\n'
    else
      exec sudo -- "$START_SH" "${FORWARD_ARGS[@]}"
    fi
    ;;
  su)
    quoted="$(printf '%q ' "$START_SH" "${FORWARD_ARGS[@]}")"
    if [ "$PRINT_COMMAND" -eq 1 ]; then
      printf 'su -p root -c %q\n' "exec $quoted"
    else
      exec su -p root -c "exec $quoted"
    fi
    ;;
esac
