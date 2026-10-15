"""Signed bootstrap requests for the federation pairing-code exchange."""
from __future__ import annotations

import hmac
import re
import secrets
import time
from typing import Any

from .federation_core import canonical_json, sanitize_peer_id
from .federation_identity import FederationIdentity, public_key_fingerprint, verify
from .federation_local_profile import local_peer_id
from .federation_store import FederationStore
from .federation_trust_store import FederationTrustStore


PAIRING_PROTOCOL = "simpleoffice-pairing-v1"
PAIRING_MAX_CLOCK_SKEW_SECONDS = 300
PAIRING_NONCE_TTL_SECONDS = 600
_NONCE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_OPERATIONS = frozenset({"claim", "status"})


def _canonical_request(value: dict[str, Any], expected_operation: str, *, now: int) -> bytes:
    if not isinstance(value, dict) or expected_operation not in _OPERATIONS:
        raise ValueError("invalid pairing request")
    if value.get("protocol") != PAIRING_PROTOCOL or value.get("operation") != expected_operation:
        raise ValueError("invalid pairing request")
    raw_peer_id = str(value.get("peer_id") or "")
    peer_id = sanitize_peer_id(raw_peer_id)
    if raw_peer_id != peer_id:
        raise ValueError("invalid pairing identity")
    public_key = str(value.get("public_key") or "").strip()[:512]
    fingerprint = str(value.get("fingerprint") or "").strip()[:256]
    if not public_key or not fingerprint or not hmac.compare_digest(
        public_key_fingerprint(public_key), fingerprint
    ):
        raise ValueError("invalid pairing identity")
    try:
        timestamp = int(value.get("timestamp"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid pairing timestamp") from exc
    if abs(now - timestamp) > PAIRING_MAX_CLOCK_SKEW_SECONDS:
        raise ValueError("invalid pairing timestamp")
    nonce = str(value.get("nonce") or "")
    if not _NONCE.fullmatch(nonce):
        raise ValueError("invalid pairing nonce")
    code = str(value.get("code") or "")
    if expected_operation == "claim":
        if not code or len(code) > 64:
            raise ValueError("invalid pairing code")
    elif code:
        raise ValueError("invalid pairing request")
    return canonical_json({
        "protocol": PAIRING_PROTOCOL,
        "operation": expected_operation,
        "peer_id": peer_id,
        "public_key": public_key,
        "fingerprint": fingerprint,
        "timestamp": timestamp,
        "nonce": nonce,
        "code": code,
    })


def build_pairing_request(
    root: str,
    operation: str,
    *,
    code: str = "",
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    """Create a request signed by this instance's persistent Ed25519 identity."""
    identity = FederationIdentity(root)
    public = identity.public_identity()
    value = {
        "protocol": PAIRING_PROTOCOL,
        "operation": operation,
        "peer_id": local_peer_id(),
        "public_key": public["public_key"],
        "fingerprint": public["fingerprint"],
        "timestamp": int(time.time()) if timestamp is None else int(timestamp),
        "nonce": nonce or secrets.token_urlsafe(24),
        "code": str(code or "") if operation == "claim" else "",
    }
    value["signature"] = identity.sign(
        _canonical_request(value, operation, now=int(value["timestamp"]))
    )
    return value


def authenticate_pairing_request(
    root: str,
    value: Any,
    operation: str,
    *,
    now: int | None = None,
) -> str:
    """Verify identity, freshness and one-time use without granting peer trust."""
    if not isinstance(value, dict):
        raise ValueError("invalid pairing request")
    checked_at = int(time.time()) if now is None else int(now)
    canonical = _canonical_request(value, operation, now=checked_at)
    peer_id = sanitize_peer_id(value.get("peer_id", ""))
    public_key = str(value.get("public_key") or "").strip()[:512]
    signature = str(value.get("signature") or "")[:1024]
    store = FederationStore(root)
    if peer_id in store.banned_peer_ids(now=checked_at):
        raise ValueError("invalid pairing signature")
    known = FederationTrustStore(root).identity(peer_id) or {}
    known_key = str(known.get("public_key") or "")
    known_fingerprint = str(known.get("fingerprint") or "")
    if (
        not known_key
        or not hmac.compare_digest(known_key, public_key)
        or not hmac.compare_digest(known_fingerprint, str(value.get("fingerprint") or ""))
        or not verify(public_key, canonical, signature)
    ):
        raise ValueError("invalid pairing signature")
    nonce = str(value.get("nonce") or "")
    if not store.claim_nonce(
        f"pairing:{peer_id}:{nonce}", checked_at + PAIRING_NONCE_TTL_SECONDS
    ):
        raise ValueError("replayed pairing request")
    return peer_id
