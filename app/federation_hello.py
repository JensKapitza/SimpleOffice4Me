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
import secrets
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
from .federation_trust_store import FederationTrustStore
from .federation_connection_invite import (
    FederationConnectionInvites, derive_peer_token, invite_proof, verify_invite_proof,
)

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
    if payload.get("invitation_id") is not None:
        narrowed.update({
            "invitation_id": payload.get("invitation_id"),
            "invitation_nonce": payload.get("invitation_nonce"),
            "invitation_proof": payload.get("invitation_proof"),
        })
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


def send_connection_invite(root: str | Path, invitation: dict[str, Any], base_url: str) -> dict[str, Any]:
    """Claim a scanned invite, then derive and save the same peer token locally."""
    peer = invitation["peer"]
    peer_id = sanitize_peer_id(peer.get("peer_id", ""))
    if not peer.get("public_key") or not peer.get("fingerprint"):
        raise ValueError("scanned invitation has no verifiable peer identity")
    pinned = FederationTrustStore(root).identity(peer_id) or {}
    pinned_fingerprint = str(pinned.get("fingerprint", "") or "")
    if pinned_fingerprint and pinned_fingerprint != peer["fingerprint"]:
        raise ValueError("scanned peer fingerprint differs from the saved identity")
    secret = invitation["secret"]
    payload = build_hello(root, base_url)
    store = FederationStore(root)
    token = derive_peer_token(secret, payload["peer_id"], peer_id)
    existing = store.get_peer(peer_id)
    nonce = secrets.token_urlsafe(18)
    payload.update({
        "invitation_id": invitation["invite_id"],
        "invitation_nonce": nonce,
    })
    payload["invitation_proof"] = invite_proof(secret, invitation["invite_id"], payload, nonce)
    payload["signature"] = FederationIdentity(root).sign(_canonical_hello(payload))
    answer = _post_json(normalize_endpoint(peer.get("base_url", "")), HELLO_PATH, payload, HELLO_TIMEOUT_SECONDS)
    if not answer.get("ok") or answer.get("paired") is not True:
        raise ValueError("peer did not complete the one-scan pairing")
    responder = answer.get("peer") if isinstance(answer.get("peer"), dict) else {}
    if (str(responder.get("peer_id", "")) != peer_id or
            str(responder.get("fingerprint", "")) != str(peer.get("fingerprint", ""))):
        raise ValueError("pairing response does not match the scanned peer")
    _identity, conflict = FederationTrustStore(root).remember_discovered_identity(
        peer_id, peer.get("country", ""), peer["fingerprint"], "one-scan-pairing",
        peer["public_key"],
    )
    if conflict:
        raise ValueError("scanned peer fingerprint differs from the saved identity")
    stored = store.save_discovered_peer(peer_id, peer.get("label", peer_id), peer["base_url"])
    enabled = bool(existing.get("enabled", True)) if existing else True
    store.save_peer(
        peer_id, stored.get("label") or peer.get("label", peer_id),
        stored.get("base_url") or peer["base_url"], token,
        policy=stored.get("policy") or {}, enabled=enabled,
    )
    store.record_event("peer_connection_invite_claimed", peer_id=peer_id, detail={})
    return {"ok": True, "peer_id": peer_id, "responder": responder}


def receive_hello(root: str | Path, payload: Any, invites: FederationConnectionInvites | None = None) -> dict[str, Any]:
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
    if payload.get("invitation_id") is not None:
        signed.update({
            "invitation_id": payload.get("invitation_id"),
            "invitation_nonce": payload.get("invitation_nonce"),
            "invitation_proof": payload.get("invitation_proof"),
        })
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
    paired = False
    paired_enabled = bool(known.get("enabled", True)) if known else True
    if payload.get("invitation_id") is not None:
        invite_id = str(payload.get("invitation_id") or "")
        nonce = str(payload.get("invitation_nonce") or "")
        if not 16 <= len(nonce) <= 128 or not isinstance(payload.get("invitation_proof"), str):
            raise ValueError("connection invitation proof is invalid")
        invite_store = invites or FederationConnectionInvites(root)
        invite_secret = invite_store.secret(
            invite_id, claimant=profile["peer_id"], fingerprint=profile["fingerprint"],
        )
        if not verify_invite_proof(invite_secret, invite_id, signed, nonce, payload.get("invitation_proof")):
            raise ValueError("connection invitation proof is invalid")
        if profile["peer_id"] in store.banned_peer_ids():
            raise ValueError("peer is blocked")
        token = derive_peer_token(invite_secret, profile["peer_id"], local_peer_id())
        if not invite_store.claim(invite_id, profile["peer_id"], profile["fingerprint"]):
            raise ValueError("connection invitation was already used or expired")
        paired = True
    remember_discovered_peer(root, profile, "hello")
    if paired:
        current = store.get_peer(profile["peer_id"]) or {}
        store.save_peer(
            profile["peer_id"], current.get("label") or profile["label"],
            current.get("base_url") or profile["base_url"], token,
            policy=current.get("policy") or {}, enabled=paired_enabled,
        )
    try:
        own = local_profile(root, "")
    except ValueError:
        public = FederationIdentity(root).public_identity()
        own = {"peer_id": local_peer_id(), "label": local_peer_id(), "base_url": "",
               "fingerprint": public["fingerprint"], "public_key": public["public_key"]}
    store.record_event("peer_hello_received", peer_id=profile["peer_id"], detail={})
    return {
        "ok": True,
        "paired": paired,
        "peer": {
            "peer_id": own.get("peer_id", ""), "label": own.get("label", ""),
            "base_url": own.get("base_url", ""), "fingerprint": own.get("fingerprint", ""),
        },
    }
