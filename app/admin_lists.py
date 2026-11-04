"""Admin allowlists from the disk store (no environment).

``security_admins`` guards security-sensitive document operations,
``document_admins`` grants WebDAV/VFS management. Both are plain username
lists (no secrets); an empty list means nobody extra (fail closed).
Administrators always keep full access through their account flag.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import app_config_store as _store

SECTION = "admin_lists"
MAX_LIST_CHARS = 4096

_ERRORS = {
    "security_admins": (
        "Security-Admin-Liste prüfen (kommagetrennte Benutzernamen).",
        "Check the security admin list (comma-separated usernames).",
    ),
    "document_admins": (
        "Dokument-Admin-Liste prüfen (kommagetrennte Benutzernamen).",
        "Check the document admin list (comma-separated usernames).",
    ),
}


def _fail(key: str, language: str) -> ValueError:
    de, en = _ERRORS[key]
    return ValueError(en if language == "en" else de)


def _names(raw: Any) -> set[str]:
    return {part.strip() for part in str(raw or "").split(",") if part.strip()}


def security_admins() -> set[str]:
    return _names(_store.get(SECTION, "security_admins", ""))


def document_admins() -> set[str]:
    return _names(_store.get(SECTION, "document_admins", ""))


def validate(values: Mapping[str, Any], language: str = "de") -> dict[str, str]:
    if not isinstance(values, dict):
        raise _fail("security_admins", language)
    cleaned: dict[str, str] = {}
    for key in ("security_admins", "document_admins"):
        if key not in values:
            continue
        raw = values[key]
        if raw is None:
            raw = ""
        if not isinstance(raw, str) or len(raw) > MAX_LIST_CHARS:
            raise _fail(key, language)
        cleaned[key] = ",".join(sorted(_names(raw)))
    return cleaned


def save(values: Mapping[str, Any], language: str = "de") -> dict[str, Any]:
    """Persist the given lists; an empty list clears it (fail closed)."""
    cleaned = validate(values, language)
    if cleaned:
        _store.put(SECTION, cleaned)
    return status()


def status() -> dict[str, Any]:
    return {
        "security_admins": ",".join(sorted(security_admins())),
        "document_admins": ",".join(sorted(document_admins())),
    }
