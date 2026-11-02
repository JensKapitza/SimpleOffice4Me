"""License master token from the disk store (no environment).

The token authenticates this installation against the license master
(outbound reports) and inbound master APIs. It is encrypted (``enc:v1:``)
in the user-home app config store, section ``"licensing"``; only a
presence marker leaves this module.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import app_config_store as _store

SECTION = "licensing"

_ERRORS = {
    "token": (
        "Master-Token prüfen (druckbares ASCII).",
        "Check the master token (printable ASCII).",
    ),
}


def _fail(language: str) -> ValueError:
    de, en = _ERRORS["token"]
    return ValueError(en if language == "en" else de)


def master_token() -> str:
    return _store.secret(SECTION, "master_token")


def master_token_configured() -> bool:
    return _store.secret_configured(SECTION, "master_token")


def validate(values: Mapping[str, Any], language: str = "de") -> dict[str, str]:
    if not isinstance(values, dict):
        raise _fail(language)
    raw = values.get("master_token", "")
    if raw in (None, ""):
        return {"master_token": ""}
    if not isinstance(raw, str) or any(ord(c) < 32 or ord(c) > 126 for c in raw):
        raise _fail(language)
    return {"master_token": raw}


def save(values: Mapping[str, Any], language: str = "de") -> dict[str, Any]:
    """Persist; an empty token keeps the stored secret (never shown)."""
    cleaned = validate(values, language)
    if cleaned["master_token"]:
        _store.put_secret(SECTION, "master_token", cleaned["master_token"])
    return status()


def status() -> dict[str, Any]:
    return {"master_token_configured": master_token_configured()}
