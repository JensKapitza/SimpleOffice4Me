"""Operator-approved, destination-bound environment credentials for mail."""
from __future__ import annotations

import json
import os
import re

BINDINGS_ENV = "SIMPLEOFFICE_MAIL_ENV_CREDENTIAL_BINDINGS"


def environment_mail_password(actor: str, account: dict, protocol: str, name: str) -> str:
    """Never let user-editable account settings select arbitrary process secrets."""
    if not name:
        return ""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,119}", name):
        raise ValueError("Ungültiger Name für die Mail-Passwortvariable.")
    raw = os.environ.get(BINDINGS_ENV, "[]")
    try:
        if len(raw.encode("utf-8")) > 64 * 1024:
            raise ValueError("binding configuration exceeds limit")
        bindings = json.loads(raw)
        if not isinstance(bindings, list) or len(bindings) > 1000:
            raise ValueError("invalid binding configuration")
    except (ValueError, TypeError):
        raise ValueError("Die administrativen Mail-Passwortbindungen sind ungültig.") from None
    fields = {
        "imap": ("host", "port", "security", "username"),
        "smtp": ("smtp_host", "smtp_port", "smtp_security", "smtp_username"),
        "sieve": ("sieve_host", "sieve_port", "sieve_security", "username"),
    }
    if protocol not in fields:
        raise ValueError("invalid mail credential protocol")
    host, port, security, username = fields[protocol]
    expected = {
        "owner": actor, "account_id": account["id"], "protocol": protocol,
        "env": name, "host": str(account[host]).casefold(), "port": account[port],
        "security": account[security], "username": account[username],
    }
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        normalized = {**binding, "host": str(binding.get("host", "")).casefold()}
        if all(normalized.get(key) == value for key, value in expected.items()):
            password = os.environ.get(name, "")
            if not password:
                raise ValueError("Die freigegebene Mail-Passwortvariable ist nicht verfügbar.")
            return password
    raise ValueError(
        "Diese Passwortvariable ist für Benutzer, Konto und Mailserver nicht administrativ freigegeben."
    )
