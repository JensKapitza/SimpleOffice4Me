"""Resource Commander credentials from the disk store (no environment).

The inbound server token and the per-peer outbound tokens are encrypted
(``enc:v1:``) in the user-home app config store, section
``"resource_commander"``; only presence markers leave this module. Outbound
peer tokens are deliberately separate from ``FederationStore.peer_token``.
"""
from __future__ import annotations

import json
import secrets
from typing import Any, Mapping

from . import app_config_store as _store
from .resource_provider import ProviderError

SECTION = "resource_commander"
MAX_TOKEN_CHARS = 4096
MAX_MAPPING_CHARS = 65536

_ERRORS = {
    "token": (
        "Commander-Token prüfen (druckbares ASCII).",
        "Check the commander token (printable ASCII).",
    ),
    "mapping": (
        "Peer-Mapping prüfen (JSON-Objekt aus Peer-ID zu Token).",
        "Check the peer mapping (JSON object of peer ID to token).",
    ),
}


def _fail(code: str, language: str) -> ValueError:
    de, en = _ERRORS[code]
    return ValueError(en if language == "en" else de)


def inbound_token() -> str:
    return _store.secret(SECTION, "token")


def inbound_token_configured() -> bool:
    return _store.secret_configured(SECTION, "token")


def peer_tokens() -> dict[str, str]:
    raw = _store.secret(SECTION, "peer_tokens")
    if not raw:
        return {}
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Resource-Commander-Peer-Token-Mapping ist ungültiges JSON") from exc
    if not isinstance(values, dict):
        raise ValueError("Resource-Commander-Peer-Token-Mapping muss ein JSON-Objekt sein")
    return {str(peer): str(token) for peer, token in values.items()}


def peer_commander_token(peer_id: str) -> str:
    """Return the outbound token for a peer; never falls back to SOFP tokens."""
    value = peer_tokens().get(str(peer_id))
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ProviderError(
        f"Für Federation-Peer {peer_id} ist kein separater Resource-Commander-Token konfiguriert"
    )


def peer_tokens_configured() -> bool:
    return _store.secret_configured(SECTION, "peer_tokens")


def rotate_token() -> str:
    """Generate, store and return a fresh inbound token (old one invalid immediately)."""
    fresh = secrets.token_urlsafe(48)
    _store.put_secret(SECTION, "token", fresh)
    return fresh


def validate(values: Mapping[str, Any], language: str = "de") -> dict[str, str]:
    if not isinstance(values, dict):
        raise _fail("token", language)
    raw_token = values.get("token", "")
    if raw_token in (None, ""):
        token = ""
    elif not isinstance(raw_token, str) or len(raw_token) > MAX_TOKEN_CHARS or any(
        ord(c) < 32 or ord(c) > 126 for c in raw_token
    ):
        raise _fail("token", language)
    else:
        token = raw_token
    raw_mapping = values.get("peer_tokens", "")
    if raw_mapping in (None, ""):
        mapping = ""
    elif not isinstance(raw_mapping, str) or len(raw_mapping) > MAX_MAPPING_CHARS:
        raise _fail("mapping", language)
    else:
        try:
            parsed = json.loads(raw_mapping)
        except json.JSONDecodeError:
            raise _fail("mapping", language) from None
        if not isinstance(parsed, dict) or any(
            not isinstance(peer, str) or not isinstance(token_value, str)
            or not token_value.strip()
            or any(ord(c) < 32 or ord(c) > 126 for c in token_value)
            for peer, token_value in parsed.items()
        ):
            raise _fail("mapping", language)
        mapping = raw_mapping
    return {"token": token, "peer_tokens": mapping}


def save(values: Mapping[str, Any], language: str = "de") -> dict[str, Any]:
    """Persist; empty fields keep the stored secrets (never shown)."""
    cleaned = validate(values, language)
    if cleaned["token"]:
        _store.put_secret(SECTION, "token", cleaned["token"])
    if cleaned["peer_tokens"]:
        _store.put_secret(SECTION, "peer_tokens", cleaned["peer_tokens"])
    return status()


def status() -> dict[str, Any]:
    return {
        "token_configured": inbound_token_configured(),
        "peer_tokens_configured": peer_tokens_configured(),
    }
