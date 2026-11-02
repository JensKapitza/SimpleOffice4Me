"""SMTP send settings from the disk store (no environment).

Used for booking confirmations (calendar) and other server-sent mail.
The password is encrypted (``enc:v1:``) in the user-home app config
store, section ``"smtp"``; only a presence marker leaves this module.
"""
from __future__ import annotations

import smtplib
from typing import Any, Mapping

from . import app_config_store as _store

SECTION = "smtp"
MAX_HOST_CHARS = 253
MAX_ADDRESS_CHARS = 320
MAX_USER_CHARS = 320
MAX_PORT = 65535

_ERRORS = {
    "smtp": (
        "SMTP prüfen (Host, Absender, Port 1-65535).",
        "Check the SMTP settings (host, sender, port 1-65535).",
    ),
    "auth": (
        "SMTP-Zugang prüfen (Benutzer, Passwort druckbar).",
        "Check the SMTP credentials (user, printable password).",
    ),
}


def _fail(code: str, language: str) -> ValueError:
    de, en = _ERRORS[code]
    return ValueError(en if language == "en" else de)


def host() -> str:
    return str(_store.get(SECTION, "host", "") or "").strip()


def sender() -> str:
    return str(_store.get(SECTION, "sender", "") or "").strip()


def port() -> int:
    try:
        value = int(str(_store.get(SECTION, "port", "587") or "587").strip())
    except ValueError:
        return 587
    return value if 1 <= value <= MAX_PORT else 587


def starttls() -> bool:
    return str(_store.get(SECTION, "starttls", "1") or "").strip().casefold() in {
        "1", "true", "yes", "on",
    }


def user() -> str:
    return str(_store.get(SECTION, "user", "") or "").strip()


def password() -> str:
    return _store.secret(SECTION, "password")


def password_configured() -> bool:
    return _store.secret_configured(SECTION, "password")


def configured() -> bool:
    return bool(host() and sender())


def _single_line(raw: Any, limit: int) -> str | None:
    if raw in (None, ""):
        return ""
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if len(value) > limit or any(ord(c) < 32 for c in value):
        return None
    return value


def validate(values: Mapping[str, Any], language: str = "de") -> dict[str, Any]:
    if not isinstance(values, dict):
        raise _fail("smtp", language)
    host_value = _single_line(values.get("host", ""), MAX_HOST_CHARS)
    sender_value = _single_line(values.get("sender", ""), MAX_ADDRESS_CHARS)
    user_value = _single_line(values.get("user", ""), MAX_USER_CHARS)
    if host_value is None or sender_value is None or user_value is None:
        raise _fail("smtp", language)
    if sender_value and ("@" not in sender_value or any(c.isspace() for c in sender_value)):
        raise _fail("smtp", language)
    try:
        port_value = int(str(values.get("port", "587") or "587").strip())
    except ValueError:
        raise _fail("smtp", language) from None
    if not 1 <= port_value <= MAX_PORT:
        raise _fail("smtp", language)
    raw_tls = values.get("starttls", "")
    if isinstance(raw_tls, bool):
        starttls_value = "1" if raw_tls else ""
    elif raw_tls in (None, ""):
        starttls_value = ""
    elif str(raw_tls).strip().casefold() in {"1", "true", "yes", "on"}:
        starttls_value = "1"
    elif str(raw_tls).strip().casefold() in {"0", "false", "no", "off"}:
        starttls_value = ""
    else:
        raise _fail("smtp", language)
    raw_password = values.get("password", "")
    if raw_password in (None, ""):
        password_value = ""
    elif not isinstance(raw_password, str) or any(ord(c) < 32 or ord(c) > 126 for c in raw_password):
        raise _fail("auth", language)
    else:
        password_value = raw_password
    return {
        "host": host_value,
        "sender": sender_value,
        "port": port_value,
        "starttls": starttls_value,
        "user": user_value,
        "password": password_value,
    }


def save(values: Mapping[str, Any], language: str = "de") -> dict[str, Any]:
    """Persist settings; an empty password keeps (never deletes) the stored secret."""
    cleaned = validate(values, language)
    _store.put(SECTION, {
        "host": cleaned["host"],
        "sender": cleaned["sender"],
        "port": cleaned["port"],
        "starttls": cleaned["starttls"],
        "user": cleaned["user"],
    })
    if cleaned["password"]:
        _store.put_secret(SECTION, "password", cleaned["password"])
    return status()


def test_login(language: str = "de") -> None:
    """Try SMTP connect (+login when a user is set) without sending mail."""
    if not configured():
        raise _fail("smtp", language)
    try:
        with smtplib.SMTP(host(), port(), timeout=20) as smtp:
            if starttls():
                smtp.starttls()
            if user():
                smtp.login(user(), password())
    except (OSError, smtplib.SMTPException) as exc:
        raise ValueError(str(exc) or ("SMTP-Test fehlgeschlagen." if language != "en" else "SMTP test failed.")) from exc


def status() -> dict[str, Any]:
    return {
        "host": host(),
        "sender": sender(),
        "port": port(),
        "starttls": starttls(),
        "user": user(),
        "password_configured": password_configured(),
        "configured": configured(),
    }
