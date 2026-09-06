"""Encrypted, replay-protected federation envelopes for credential changes."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .federation_core import canonical_json, sanitize_peer_id
from .federation_store import FederationStore

CONTEXT = b"SimpleOffice4Me/federation-credential/v1"
MAX_TTL_SECONDS = 300


def _derive_key(shared_secret: str, sender_peer: str, receiver_peer: str) -> bytes:
    sender = sanitize_peer_id(sender_peer)
    receiver = sanitize_peer_id(receiver_peer)
    secret = shared_secret.encode("utf-8")
    if len(secret) < 24:
        raise ValueError("Federation secret is too short")
    salt = hashlib.sha256((sender + "\0" + receiver).encode("utf-8") + CONTEXT).digest()
    return hmac.new(salt, secret, hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def make_password_change_envelope(
    *,
    shared_secret: str,
    sender_peer: str,
    receiver_peer: str,
    username: str,
    password: str,
    contact_id: str = "",
    expires_in: int = 120,
) -> dict[str, Any]:
    ttl = max(30, min(int(expires_in), MAX_TTL_SECONDS))
    now = int(time.time())
    nonce_id = _b64(os.urandom(18))
    header = {
        "version": 1,
        "type": "password_change",
        "sender_peer": sanitize_peer_id(sender_peer),
        "receiver_peer": sanitize_peer_id(receiver_peer),
        "created_at": now,
        "expires_at": now + ttl,
        "nonce_id": nonce_id,
    }
    payload = {
        "username": str(username),
        "password": str(password),
        "contact_id": str(contact_id)[:160],
    }
    nonce = os.urandom(12)
    key = _derive_key(shared_secret, header["sender_peer"], header["receiver_peer"])
    ciphertext = AESGCM(key).encrypt(nonce, canonical_json(payload), canonical_json(header))
    return {"header": header, "nonce": _b64(nonce), "ciphertext": _b64(ciphertext)}


def open_password_change_envelope(
    envelope: dict[str, Any],
    *,
    shared_secret: str,
    local_peer: str,
    store: FederationStore,
    now: int | None = None,
) -> dict[str, str]:
    if not isinstance(envelope, dict) or not isinstance(envelope.get("header"), dict):
        raise ValueError("invalid credential envelope")
    header = envelope["header"]
    current = int(time.time()) if now is None else int(now)
    if header.get("version") != 1 or header.get("type") != "password_change":
        raise ValueError("unsupported credential envelope")
    sender = sanitize_peer_id(str(header.get("sender_peer") or ""))
    receiver = sanitize_peer_id(str(header.get("receiver_peer") or ""))
    if receiver != sanitize_peer_id(local_peer):
        raise ValueError("credential envelope addressed to another peer")
    expires_at = int(header.get("expires_at") or 0)
    created_at = int(header.get("created_at") or 0)
    if created_at > current + 30 or expires_at < current or expires_at - created_at > MAX_TTL_SECONDS:
        raise ValueError("credential envelope expired")
    nonce_id = str(header.get("nonce_id") or "")[:240]
    if not store.claim_nonce("credential:" + nonce_id, expires_at):
        raise ValueError("credential envelope replay detected")
    try:
        nonce = _unb64(str(envelope.get("nonce") or ""))
        ciphertext = _unb64(str(envelope.get("ciphertext") or ""))
        key = _derive_key(shared_secret, sender, receiver)
        raw = AESGCM(key).decrypt(nonce, ciphertext, canonical_json(header))
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError("credential envelope authentication failed") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid credential payload")
    username = str(payload.get("username") or "")
    password = str(payload.get("password") or "")
    contact_id = str(payload.get("contact_id") or "")[:160]
    if not username or not password:
        raise ValueError("credential payload incomplete")
    return {"username": username, "password": password, "contact_id": contact_id, "sender_peer": sender}
