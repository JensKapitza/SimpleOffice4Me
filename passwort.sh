#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || -z "${1//[[:space:]]/}" ]]; then
    echo "Verwendung: $0 <username>" >&2
    exit 2
fi

USERNAME="$1"
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ -n "${PYTHON:-}" ]]; then
    PYTHON_BIN="$PYTHON"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
elif [[ -x "$ROOT_DIR/venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
else
    echo "Fehler: Kein Python-Interpreter gefunden." >&2
    exit 4
fi

exec "$PYTHON_BIN" - "$USERNAME" <<'PY'
from __future__ import annotations

import getpass
import sys

from app import app
from app.db import get_db
from app.password_security import hash_password

username = sys.argv[1].strip()
if not username:
    print("Fehler: Benutzername darf nicht leer sein.", file=sys.stderr)
    raise SystemExit(2)

with app.app_context():
    db = get_db()
    user = db.execute(
        "SELECT id, username, is_disabled FROM user WHERE username = ?",
        (username,),
    ).fetchone()
    if user is None:
        print(f"Fehler: Benutzer '{username}' wurde nicht gefunden.", file=sys.stderr)
        raise SystemExit(3)

    password = getpass.getpass(f"Neues Passwort für {user['username']}: ")
    confirmation = getpass.getpass("Passwort wiederholen: ")

    if password != confirmation:
        print("Fehler: Die Passwörter stimmen nicht überein.", file=sys.stderr)
        raise SystemExit(5)
    if len(password) < 12:
        print("Fehler: Das Passwort muss mindestens 12 Zeichen haben.", file=sys.stderr)
        raise SystemExit(6)
    if len(password) > 128:
        print("Fehler: Das Passwort darf höchstens 128 Zeichen haben.", file=sys.stderr)
        raise SystemExit(6)

    db.execute(
        """
        UPDATE user
           SET password = ?,
               auth_version = auth_version + 1,
               updated_at = CURRENT_TIMESTAMP
         WHERE id = ?
        """,
        (hash_password(password), user["id"]),
    )
    db.commit()

    print(f"Passwort für '{user['username']}' wurde zurückgesetzt.")
    print("Bestehende Sitzungen wurden ungültig gemacht.")
    if user["is_disabled"]:
        print("Hinweis: Das Benutzerkonto ist weiterhin gesperrt.")
PY
