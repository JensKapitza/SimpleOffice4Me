"""Marketplace and OAuth credentials from the disk store (no environment).

Amazon Product Advertising, eBay Browse and Google OAuth/Calendar/Drive
settings live in the user-home app config store, section ``"marketplace"``.
Secrets (API secret keys, client secrets, calendar account JSON) are
encrypted (``enc:v1:``); only presence markers leave this module.

Only ``google_oauth_credentials()`` tolerates a missing Flask application
context (file fallback still works); all other secret getters fail closed
with ``RuntimeError`` because unlocking needs the application key.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from flask import has_app_context

from . import app_config_store as _store

SECTION = "marketplace"
MAX_TEXT_CHARS = 2048
MAX_JSON_CHARS = 65536
DEFAULT_EBAY_MARKETPLACE_ID = "EBAY_DE"
DEFAULT_DRIVE_MAX_MIB = 128
MAX_DRIVE_MAX_MIB = 512

_ERRORS = {
    "marketplace": (
        "Anbindung prüfen (einzeilige Texte, Marktplatz-Kennung).",
        "Check the integration (single-line texts, marketplace ID).",
    ),
    "auth": (
        "Zugangsdaten prüfen (druckbares ASCII).",
        "Check the credentials (printable ASCII).",
    ),
}


def _fail(code: str, language: str) -> ValueError:
    de, en = _ERRORS[code]
    return ValueError(en if language == "en" else de)


def _raw(name: str) -> str:
    return str(_store.get(SECTION, name, "") or "").strip()


def _secret(name: str) -> str:
    return _store.secret(SECTION, name)


def amazon_credentials() -> tuple[str, str, str]:
    return _raw("amazon_access_key"), _secret("amazon_secret_key"), _raw("amazon_partner_tag")


def amazon_access_key() -> str:
    return _raw("amazon_access_key")


def amazon_partner_tag() -> str:
    return _raw("amazon_partner_tag")


def ebay_credentials() -> tuple[str, str]:
    return _raw("ebay_client_id"), _secret("ebay_client_secret")


def ebay_client_id() -> str:
    return _raw("ebay_client_id")


def ebay_marketplace_id() -> str:
    return _raw("ebay_marketplace_id") or DEFAULT_EBAY_MARKETPLACE_ID


def google_client_id() -> str:
    return _raw("google_client_id")


def google_client_secret() -> str:
    return _secret("google_client_secret")


def google_credentials_file() -> str:
    """Stored path, else the starter transport (``start.sh --google-json``).

    Only the file *path* falls back to environment (bootstrap transport like
    DOCUMENT_ROOT); secrets never do.
    """
    return _raw("google_credentials_file") or os.environ.get(
        "SIMPLEOFFICE_GOOGLE_CREDENTIALS_FILE", ""
    ).strip()


def google_oauth_web_config() -> dict[str, Any]:
    """Return the ``web`` block from the stored Google OAuth JSON file."""
    credentials_file = google_credentials_file()
    if not credentials_file:
        return {}
    try:
        credentials = json.loads(Path(credentials_file).read_text(encoding="utf-8"))
        web = credentials["web"]
        if not isinstance(web, dict):
            raise TypeError("web must be an object")
        return web
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Invalid Google credentials file; expected Google Web OAuth JSON") from exc


def google_oauth_credentials() -> tuple[str, str]:
    """Client ID/secret from the store, falling back to the OAuth JSON file.

    Without a Flask application context (import time, plain unit tests) the
    encrypted secret cannot be unlocked; the file fallback still works then.
    """
    web = google_oauth_web_config()
    client_id = google_client_id()
    client_secret = google_client_secret() if has_app_context() else ""
    if not web:
        return client_id, client_secret
    try:
        file_client_id = str(web["client_id"]).strip()
        file_client_secret = str(web["client_secret"]).strip()
    except (KeyError, TypeError) as exc:
        raise RuntimeError("Google OAuth JSON requires web.client_id and web.client_secret") from exc
    return client_id or file_client_id, client_secret or file_client_secret


def google_oauth_redirect_uris() -> tuple[str, ...]:
    """Callback URIs declared in the Google OAuth JSON file (no secret needed)."""
    values = google_oauth_web_config().get("redirect_uris", [])
    if not isinstance(values, list):
        return ()
    return tuple(str(value).strip() for value in values if isinstance(value, str) and value.strip())


def google_calendar_accounts_raw() -> str:
    return _secret("google_calendar_accounts_json")


def google_drive_max_mib() -> int:
    try:
        mib = int(_raw("google_drive_max_mib") or DEFAULT_DRIVE_MAX_MIB)
    except ValueError:
        mib = DEFAULT_DRIVE_MAX_MIB
    return max(1, min(mib, MAX_DRIVE_MAX_MIB))


def amazon_secret_configured() -> bool:
    return _store.secret_configured(SECTION, "amazon_secret_key")


def ebay_secret_configured() -> bool:
    return _store.secret_configured(SECTION, "ebay_client_secret")


def google_secret_configured() -> bool:
    return _store.secret_configured(SECTION, "google_client_secret")


def google_accounts_configured() -> bool:
    return _store.secret_configured(SECTION, "google_calendar_accounts_json")


def _text(raw: Any, limit: int) -> str | None:
    if raw in (None, ""):
        return ""
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if len(value) > limit or any(ord(c) < 32 for c in value):
        return None
    return value


def _ascii_secret(raw: Any) -> str | None:
    if raw in (None, ""):
        return ""
    if not isinstance(raw, str) or any(ord(c) < 32 or ord(c) > 126 for c in raw):
        return None
    return raw


def validate(values: Mapping[str, Any], language: str = "de") -> dict[str, Any]:
    if not isinstance(values, dict):
        raise _fail("marketplace", language)
    texts = {
        "amazon_access_key": _text(values.get("amazon_access_key", ""), MAX_TEXT_CHARS),
        "amazon_partner_tag": _text(values.get("amazon_partner_tag", ""), MAX_TEXT_CHARS),
        "ebay_client_id": _text(values.get("ebay_client_id", ""), MAX_TEXT_CHARS),
        "ebay_marketplace_id": _text(values.get("ebay_marketplace_id", ""), MAX_TEXT_CHARS),
        "google_client_id": _text(values.get("google_client_id", ""), MAX_TEXT_CHARS),
        "google_credentials_file": _text(values.get("google_credentials_file", ""), MAX_TEXT_CHARS),
    }
    if any(value is None for value in texts.values()):
        raise _fail("marketplace", language)
    secrets = {
        "amazon_secret_key": _ascii_secret(values.get("amazon_secret_key", "")),
        "ebay_client_secret": _ascii_secret(values.get("ebay_client_secret", "")),
        "google_client_secret": _ascii_secret(values.get("google_client_secret", "")),
    }
    if any(value is None for value in secrets.values()):
        raise _fail("auth", language)
    accounts_raw = values.get("google_calendar_accounts_json", "")
    if accounts_raw in (None, ""):
        accounts = ""
    elif not isinstance(accounts_raw, str) or len(accounts_raw) > MAX_JSON_CHARS:
        raise _fail("auth", language)
    else:
        try:
            parsed = json.loads(accounts_raw)
        except json.JSONDecodeError:
            raise _fail("auth", language) from None
        if not isinstance(parsed, dict):
            raise _fail("auth", language)
        accounts = accounts_raw
    try:
        drive_mib = int(str(values.get("google_drive_max_mib", "") or DEFAULT_DRIVE_MAX_MIB).strip())
    except ValueError:
        raise _fail("marketplace", language) from None
    if not 1 <= drive_mib <= MAX_DRIVE_MAX_MIB:
        raise _fail("marketplace", language)
    cleaned = dict(texts)
    cleaned.update({key: value for key, value in secrets.items()})
    cleaned["google_calendar_accounts_json"] = accounts
    cleaned["google_drive_max_mib"] = drive_mib
    return cleaned


def save(values: Mapping[str, Any], language: str = "de") -> dict[str, Any]:
    """Persist settings; empty secrets keep the stored values (never shown)."""
    cleaned = validate(values, language)
    _store.put(SECTION, {
        "amazon_access_key": cleaned["amazon_access_key"],
        "amazon_partner_tag": cleaned["amazon_partner_tag"],
        "ebay_client_id": cleaned["ebay_client_id"],
        "ebay_marketplace_id": cleaned["ebay_marketplace_id"],
        "google_client_id": cleaned["google_client_id"],
        "google_credentials_file": cleaned["google_credentials_file"],
        "google_drive_max_mib": cleaned["google_drive_max_mib"],
    })
    for key in ("amazon_secret_key", "ebay_client_secret", "google_client_secret",
                "google_calendar_accounts_json"):
        if cleaned[key]:
            _store.put_secret(SECTION, key, cleaned[key])
    return status()


def status() -> dict[str, Any]:
    return {
        "amazon_access_key": _raw("amazon_access_key"),
        "amazon_partner_tag": _raw("amazon_partner_tag"),
        "amazon_secret_configured": amazon_secret_configured(),
        "ebay_client_id": _raw("ebay_client_id"),
        "ebay_marketplace_id": ebay_marketplace_id(),
        "ebay_secret_configured": ebay_secret_configured(),
        "google_client_id": google_client_id(),
        "google_secret_configured": google_secret_configured(),
        "google_credentials_file": google_credentials_file(),
        "google_accounts_configured": google_accounts_configured(),
        "google_drive_max_mib": google_drive_max_mib(),
    }
