"""Mutual peer announcement (hello) after QR scan or direct discovery.

Scanning only stored the peer locally, so pairing stayed one-sided: the
scanned peer never learned about the scanner. A hello fixes that without
granting trust: the receiver stores the announcer as a disabled,
unverified peer (visible in the list, explicit admin confirmation still
required for everything else).

Security properties:
- The announcer signs its canonical profile with its Ed25519 identity key;
  the receiver verifies the signature against the presented public key and
  checks that the fingerprint matches the key.
- Impersonation of an already known peer_id with a different fingerprint
  is rejected and recorded, never overwritten.
- Replay is harmless (idempotent registration of the same identity).
- Requests are rate-limited per claiming peer_id; responses carry no-store.
"""
from __future__ import annotations

import http.client
import json
import time
from pathlib import Path
from typing import Any

from .federation_core import sanitize_peer_id
from .federation_discovery_endpoint import (
    _connection_for,
    _host_header,
    _validated_target,
    normalize_endpoint,
)
from .federation_discovery_service import remember_discovered_peer
from .federation_identity import FederationIdentity, public_key_fingerprint, verify
from .federation_local_profile import local_peer_id, local_profile
from .federation_peer_profile import peer_profile
from .federation_peer_schema import ensure_schema
from .federation_store import FederationStore

HELLO_PATH = "/federation/v1/discovery/hello"
HELLO_TIMEOUT_SECONDS = 8
HELLO_MAX_SKEW_SECONDS = 300
HELLO_RATE_LIMIT = 10
HELLO_RATE_WINDOW_SECONDS = 60
HELLO_SIGNED_FIELDS = (
    "peer_id", "label", "base_url", "country", "fingerprint",
    "public_key", "timestamp",
)


def _canonical_hello(payload: dict[str, Any]) -> bytes:
    narrowed = {field: payload.get(field) for field in HELLO_SIGNED_FIELDS}
    return json.dumps(narrowed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_hello(root: str | Path, base_url: str = "") -> dict[str, Any]:
    """Build a signed hello payload presenting the local identity."""
    profile = local_profile(root if root is not None else None, base_url)
    identity = FederationIdentity(root)
    public = identity.public_identity()
    payload = {
        "peer_id": local_peer_id(),
        "label": str(profile.get("label", "") or local_peer_id())[:160],
        "base_url": normalize_endpoint(profile.get("base_url", "")),
        "country": str(profile.get("country", "") or "")[:2].upper(),
        "fingerprint": public["fingerprint"],
        "public_key": public["public_key"],
        "timestamp": int(time.time()),
    }
    payload["signature"] = identity.sign(_canonical_hello(payload))
    return payload


def _post_json(base_url: str, path: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    """POST over a pinned, policy-checked endpoint (no urllib opener)."""
    _canonical, parsed, port, addresses = _validated_target(base_url)
    if not addresses:
        raise ValueError("hello: peer address did not resolve")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Host": _host_header(parsed.hostname or "", port, parsed.scheme),
        "User-Agent": "SimpleOffice4Me-Federation-Hello/1",
    }
    target = str(parsed.path or "").rstrip("/") + path
    connection = _connection_for(parsed.scheme, addresses[0].compressed, port, parsed.hostname or "", timeout)
    try:
        connection.request("POST", target, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read(65536 + 1)
    except (OSError, http.client.HTTPException, TimeoutError) as exc:
        raise ValueError(f"hello transport failed: {type(exc).__name__}") from exc
    finally:
        try:
            connection.close()
        except OSError:
            pass
    if len(raw) > 65536:
        raise ValueError("hello response is too large")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("hello response is not JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("hello response is not a JSON object")
    return value


def announce_to_profile(root: str | Path, profile: dict[str, Any]) -> tuple[bool, str]:
    """Best-effort hello after a scan; returns (answered, detail).

    Never raises: failures return ``(False, reason)`` so the import flash
    can distinguish mutual visibility from local-only storage.
    """
    try:
        answer = send_hello(root, profile)
    except Exception as exc:
        return False, str(exc)[:200]
    responder = answer.get("responder", {}) if isinstance(answer, dict) else {}
    peer_id = str(responder.get("peer_id", "") or "")[:128]
    return True, peer_id or str(profile.get("peer_id", ""))


def report_hello_import(root: str | Path, profile: dict[str, Any], flash_fn, translate_fn, language, logger) -> None:
    """Run the post-scan hello and flash the outcome (keeps callers small)."""
    answered, detail = announce_to_profile(root, profile)
    if answered:
        flash_fn(translate_fn(language, "fedpeer.qr.hello.ok").format(
            peer=profile["peer_id"], responder=detail))
    else:
        logger.warning("Federation hello to %s failed: %s", profile["peer_id"], detail)
        flash_fn(translate_fn(language, "fedpeer.qr.hello.failed").format(
            peer=profile["peer_id"], exc=detail))


def send_hello(root: str | Path, peer: dict[str, Any]) -> dict[str, Any]:
    """Announce the local peer to a scanned peer and verify the answer.

    Returns ``{"ok": True, "responder": {...}}`` when the remote side
    stored the announcement, including a fingerprint cross-check against
    the scanned profile. Raises with a stage-tagged message otherwise.
    """
    peer_id = sanitize_peer_id(peer.get("peer_id", ""))
    try:
        base_url = normalize_endpoint(peer.get("base_url", ""))
    except ValueError as exc:
        raise ValueError(f"hello: invalid peer URL: {exc}") from exc
    try:
        payload = build_hello(root)
    except ValueError as exc:
        raise ValueError(f"hello: local identity incomplete: {exc}") from exc
    try:
        answer = _post_json(base_url, HELLO_PATH, payload, HELLO_TIMEOUT_SECONDS)
    except (OSError, ValueError, TimeoutError) as exc:
        raise ValueError(f"hello: no answer from peer: {type(exc).__name__}") from exc
    if not answer.get("ok"):
        raise ValueError(f"hello: peer rejected announcement: {str(answer.get('error', 'rejected'))[:120]}")
    responder = answer.get("peer") if isinstance(answer.get("peer"), dict) else {}
    expected = str(peer.get("fingerprint", "") or "")
    seen = str(responder.get("fingerprint", "") or "")
    if expected and seen and expected != seen:
        raise ValueError("hello: responder fingerprint differs from scanned profile")
    FederationStore(root).record_event(
        "peer_hello_answered", peer_id=peer_id,
        detail={"responder": str(responder.get("peer_id", ""))[:128]},
    )
    return {"ok": True, "peer_id": peer_id, "responder": responder}


def receive_hello(root: str | Path, payload: Any) -> dict[str, Any]:
    """Validate and store an incoming hello; return the local identity."""
    if not isinstance(payload, dict):
        raise ValueError("hello payload must be an object")
    profile = peer_profile(payload)
    signature = str(payload.get("signature", "") or "")
    try:
        timestamp = int(payload.get("timestamp", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("hello timestamp is invalid") from exc
    if abs(int(time.time()) - timestamp) > HELLO_MAX_SKEW_SECONDS:
        raise ValueError("hello timestamp is outside the accepted window")
    signed = {**profile, "timestamp": timestamp}
    if not verify(profile["public_key"], _canonical_hello(signed), signature):
        raise ValueError("hello signature is invalid")
    store = FederationStore(root)
    ensure_schema(store)
    if not store.claim_hello_attempt(
        profile["fingerprint"], limit=HELLO_RATE_LIMIT, window_seconds=HELLO_RATE_WINDOW_SECONDS,
    ):
        raise ValueError("hello rate limit exceeded, try again later")
    known = store.get_peer(profile["peer_id"])
    if known is not None:
        trust_row = None
        try:
            from .federation_trust_store import FederationTrustStore
            trust_row = FederationTrustStore(root).identity(profile["peer_id"]) or {}
        except (OSError, ValueError):
            trust_row = {}
        known_fingerprint = str(trust_row.get("fingerprint", "") or "")
        if known_fingerprint and known_fingerprint != profile["fingerprint"]:
            store.record_event(
                "peer_hello_rejected", peer_id=profile["peer_id"],
                detail={"reason": "fingerprint_mismatch"},
            )
            raise ValueError("hello claims a known peer with a different fingerprint")
    remember_discovered_peer(root, profile, "hello")
    try:
        own = local_profile(root, "")
    except ValueError:
        own = {"peer_id": local_peer_id(), "label": local_peer_id(), "base_url": "",
               "fingerprint": "", "public_key": ""}
    store.record_event("peer_hello_received", peer_id=profile["peer_id"], detail={})
    return {
        "ok": True,
        "peer": {
            "peer_id": own.get("peer_id", ""), "label": own.get("label", ""),
            "base_url": own.get("base_url", ""), "fingerprint": own.get("fingerprint", ""),
        },
    }
