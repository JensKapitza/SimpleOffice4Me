#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${SIMPLEOFFICE_ENV_FILE:-/etc/simpleoffice4me/simpleoffice.env}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Bitte als root ausfuehren: sudo $0" >&2
  exit 2
fi

printf 'Lizenz-Master/Federation-Token: ' >&2
IFS= read -r -s TOKEN
printf '\n' >&2

if [ -z "$TOKEN" ]; then
  echo "Token darf nicht leer sein." >&2
  exit 2
fi
if [ "${#TOKEN}" -lt 24 ]; then
  echo "Token ist zu kurz; mindestens 24 Zeichen verwenden." >&2
  exit 2
fi
if printf '%s' "$TOKEN" | grep -q '[[:cntrl:]]'; then
  echo "Token enthaelt unzulaessige Steuerzeichen." >&2
  exit 2
fi

install -d -m 0750 -o root -g simpleoffice "$(dirname -- "$ENV_FILE")"
if [ ! -f "$ENV_FILE" ]; then
  install -m 0640 -o root -g simpleoffice /dev/null "$ENV_FILE"
else
  chmod 0640 "$ENV_FILE"
  chown root:simpleoffice "$ENV_FILE"
fi

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
awk '!/^SIMPLEOFFICE_LICENSE_MASTER_TOKEN=/' "$ENV_FILE" > "$TMP"
printf 'SIMPLEOFFICE_LICENSE_MASTER_TOKEN=%s\n' "$TOKEN" >> "$TMP"
install -m 0640 -o root -g simpleoffice "$TMP" "$ENV_FILE"
unset TOKEN

systemctl try-restart simpleoffice4me.service >/dev/null 2>&1 || true
printf 'Lizenz-Token wurde lokal in %s gespeichert.\n' "$ENV_FILE"
printf 'Der Wert wurde nicht als Kommandozeilenargument verwendet und gehoert nicht ins Repository.\n'
