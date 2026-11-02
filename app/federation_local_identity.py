"""Local federation identity from the disk store (no environment).

Holds the own peer id plus the shared bearer Bear and the directory Bearer
The peer id falls back to the machine hostname when nothing is stored;
tokens fail closed to "". Secrets are encrypted (``enc:v1:``) in the
user-home app config store, section ``"federation"``. Decrypting a token
needs the Flask application context (application key); callers without one
get a ``RuntimeError`` instead of a silent empty value. Only presence
markers (``token_configured``) ever leave this module towards templates
or status payloads, never secret values. Public profile fields (label,
country, public URL, public key, fingerprint) are published to discovery
peers anyway and may be shown.
"""
from __future__ import annotations

import secrets
import socket
from typing import Any, Mapping

from . import app_config_store as _store

SECTION = "federation"
MAX_PEER_ID_CHARS = 128
MAX_TOKEN_CHARS = 4096
MAX_LABEL_CHARS = 160
MAX_PUBLIC_URL_CHARS = 512
MAX_PUBLIC_KEY_CHARS = 512
MAX_FINGERPRINT_CHARS = 256

_ERRORS = {
    "peer": ("Peer-ID prüfen (druckbar, max. 128 Zeichen).", "Check the peer ID (printable, max 128 chars)."),
    "token": ("Token muss druckbares ASCII sein.", "Token must be printable ASCII."),
    "profile": ("Profil prüfen (Label einzeilig, Land 2 Buchstaben, URL einzeilig).", "Check the profile (single-line label, 2-letter country, single-line URL)."),
}


def _fail(code: str, language: str) -> ValueError:
    de, en = _ERRORS[code]
    return ValueError(en if language == "en" else de)


def peer_id() -> str:
    """Own peer id: stored value, else the machine hostname (never empty)."""
    return str(_store.get(SECTION, "peer_id", "") or "").strip() or socket.gethostname()


def shared_token() -> str:
    """Shared bearer Bear for federation control calls ("" when unset)."""
    return _store.secret(SECTION, "token")


def directory_token() -> str:
    """Directory bearer Bear ("" when unset)."""
    return _store.secret(SECTION, "directory_token")


def label() -> str:
    """Display label for the public profile ("" when unset, caller falls back to peer id)."""
    return str(_store.get(SECTION, "label", "") or "").strip()


def country() -> str:
    """ISO-3166 alpha-2 country ("" when unset)."""
    return str(_store.get(SECTION, "country", "") or "").strip().upper()


def public_url() -> str:
    """Public base URL for published discovery ("" when unset)."""
    return str(_store.get(SECTION, "public_url", "") or "").strip()


def public_key() -> str:
    """Public key material for the public profile ("" when unset, never secret)."""
    return str(_store.get(SECTION, "public_key", "") or "").strip()


def fingerprint() -> str:
    """Key fingerprint for the public profile ("" when unset, never secret)."""
    return str(_store.get(SECTION, "fingerprint", "") or "").strip()


def token_configured() -> bool:
    return _store.secret_configured(SECTION, "token")


def directory_token_configured() -> bool:
    return _store.secret_configured(SECTION, "directory_token")


def _single_line(raw: Any, limit: int) -> str | None:
    if raw in (None, ""):
        return ""
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if len(value) > limit or any(ord(c) < 32 for c in value):
        return None
    return value


def validate(values: Mapping[str, Any], language: str = "de") -> dict[str, str]:
    if not isinstance(values, dict):
        raise _fail("peer", language)
    peer = str(values.get("peer_id", "") or "").strip()
    if len(peer) > MAX_PEER_ID_CHARS or any(ord(c) < 32 for c in peer):
        raise _fail("peer", language)
    cleaned = {"peer_id": peer, "token": "", "directory_token": ""}
    for key in ("token", "directory_token"):
        raw = values.get(key, "")
        if raw in (None, ""):
            continue
        if not isinstance(raw, str) or len(raw) > MAX_TOKEN_CHARS or any(
            ord(c) < 32 or ord(c) > 126 for c in raw
        ):
            raise _fail("token", language)
        cleaned[key] = raw
    profile = {
        "label": _single_line(values.get("label", ""), MAX_LABEL_CHARS),
        "public_url": _single_line(values.get("public_url", ""), MAX_PUBLIC_URL_CHARS),
        "public_key": _single_line(values.get("public_key", ""), MAX_PUBLIC_KEY_CHARS),
        "fingerprint": _single_line(values.get("fingerprint", ""), MAX_FINGERPRINT_CHARS),
    }
    if any(value is None for value in profile.values()):
        raise _fail("profile", language)
    cleaned.update(profile)
    raw_country = values.get("country", "")
    if raw_country in (None, ""):
        cleaned["country"] = ""
    else:
        code = str(raw_country).strip().upper()
        if len(code) != 2 or not code.isalpha() or not code.isascii():
            raise _fail("profile", language)
        cleaned["country"] = code
    return cleaned


def save(values: Mapping[str, Any], language: str = "de") -> dict[str, Any]:
    """Persist identity; empty tokens delete the stored secret, empty peer id resets to hostname."""
    cleaned = validate(values, language)
    _store.put(SECTION, {
        "peer_id": cleaned["peer_id"],
        "label": cleaned["label"],
        "country": cleaned["country"],
        "public_url": cleaned["public_url"],
        "public_key": cleaned["public_key"],
        "fingerprint": cleaned["fingerprint"],
    })
    for key in ("token", "directory_token"):
        if cleaned[key]:
            _store.put_secret(SECTION, key, cleaned[key])
        else:
            _store.discard_secret(SECTION, key)
    return status()


def status() -> dict[str, Any]:
    """Public identity state (booleans for secrets, plain values for public profile fields)."""
    return {
        "peer_id": peer_id(),
        "label": label(),
        "country": country(),
        "public_url": public_url(),
        "public_key": public_key(),
        "fingerprint": fingerprint(),
        "token_configured": token_configured(),
        "directory_token_configured": directory_token_configured(),
    }


def rotate_token() -> str:
    """Generate, store and return a fresh shared Bearer (shown once in the UI)."""
    fresh = secrets.token_urlsafe(48)
    _store.put_secret(SECTION, "token", fresh)
    return fresh
