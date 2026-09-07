"""Peer-bound authentication helpers for federation print jobs.

The normal federation bearer token authenticates the receiving server. Printing
also needs to know *which* peer is sending because receive/storage policy is
peer-specific. The sender therefore signs a short request description with its
own federation token. A receiver already stores that token for calls back to the
peer, so it can derive the source peer from the credential instead of trusting a
caller-controlled peer-id header.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .federation_store import FederationStore


AUTH_VERSION = "SOFP-PRINT-V1"
MAX_CLOCK_SKEW_SECONDS = 5 * 60
NONCE_TTL_SECONDS = 10 * 60
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


@dataclass(frozen=True)
class PrintRequestProof:
    peer_id: str
    timestamp: int
    nonce: str
    printer_id: str
    policy_revision: str
    retention_ceiling: str
    ttl_ceiling_seconds: int
    content_type: str
    filename: str
    payload_sha256: str
    payload_size: int


def new_nonce() -> str:
    return secrets.token_urlsafe(24)


def canonical_request(proof: PrintRequestProof) -> bytes:
    fields = (
        AUTH_VERSION,
        proof.peer_id,
        str(proof.timestamp),
        proof.nonce,
        proof.printer_id,
        proof.policy_revision,
        proof.retention_ceiling,
        str(proof.ttl_ceiling_seconds),
        proof.content_type,
        proof.filename,
        proof.payload_sha256,
        str(proof.payload_size),
    )
    if any("\n" in str(value) or "\r" in str(value) for value in fields):
        raise ValueError("Federation-Drucksignatur enthält unzulässige Zeilenumbrüche")
    return ("\n".join(fields) + "\n").encode("utf-8")


def sign_request(proof: PrintRequestProof, token: str) -> str:
    secret = str(token or "")
    if not secret:
        raise ValueError("Peer-spezifischer Federation-Token fehlt")
    return hmac.new(secret.encode("utf-8"), canonical_request(proof), hashlib.sha256).hexdigest()


def valid_timestamp(value: int, *, now: int | None = None) -> bool:
    current = int(time.time()) if now is None else int(now)
    return abs(current - int(value)) <= MAX_CLOCK_SKEW_SECONDS


def parse_proof_headers(headers: Any, printer_id: str) -> tuple[dict[str, Any], str]:
    try:
        timestamp = int(str(headers.get("X-SimpleOffice-Print-Timestamp", "") or ""))
        payload_size = int(str(headers.get("X-SimpleOffice-Payload-Size", "") or ""))
        ttl_ceiling = int(str(headers.get("X-SimpleOffice-TTL-Ceiling", "0") or "0"))
    except ValueError as exc:
        raise ValueError("Ungültige numerische Drucksignatur-Header") from exc
    nonce = str(headers.get("X-SimpleOffice-Print-Nonce", "") or "").strip()
    payload_sha256 = str(headers.get("X-SimpleOffice-Payload-SHA256", "") or "").strip().casefold()
    signature = str(headers.get("X-SimpleOffice-Print-Signature", "") or "").strip().casefold()
    if not NONCE_RE.fullmatch(nonce):
        raise ValueError("Ungültiger oder fehlender Druck-Nonce")
    if not SHA256_RE.fullmatch(payload_sha256):
        raise ValueError("Ungültiger oder fehlender Payload-SHA256")
    if not SHA256_RE.fullmatch(signature):
        raise ValueError("Ungültige oder fehlende Peer-Drucksignatur")
    if payload_size < 0:
        raise ValueError("Ungültige Payload-Größe")
    if ttl_ceiling < 0:
        raise ValueError("Ungültige TTL-Obergrenze")
    if not valid_timestamp(timestamp):
        raise ValueError("Federation-Drucksignatur ist abgelaufen oder noch nicht gültig")
    return {
        "timestamp": timestamp,
        "nonce": nonce,
        "printer_id": str(printer_id),
        "policy_revision": str(headers.get("X-SimpleOffice-Policy-Revision", "") or "").strip(),
        "retention_ceiling": str(headers.get("X-SimpleOffice-Retention-Ceiling", "") or "").strip().casefold(),
        "ttl_ceiling_seconds": ttl_ceiling,
        "content_type": str(headers.get("X-SimpleOffice-Content-Type", "application/octet-stream") or "application/octet-stream").split(";", 1)[0].strip()[:200],
        "filename": str(headers.get("X-SimpleOffice-Filename", "") or "").strip()[:240],
        "payload_sha256": payload_sha256,
        "payload_size": payload_size,
    }, signature


def _printing_receive_allowed(peer: dict[str, Any]) -> bool:
    policy = peer.get("policy") or {}
    printing = policy.get("printing", {}) if isinstance(policy, dict) else {}
    return bool(peer.get("enabled") and isinstance(printing, dict) and printing.get("receive") is True)


def identify_source_peer(
    store: FederationStore,
    proof_values: dict[str, Any],
    signature: str,
) -> tuple[str, PrintRequestProof]:
    """Derive the source peer by verifying the signature with peer credentials.

    The optional X-SimpleOffice-Peer-ID header is intentionally not used here.
    Policy is selected only after a unique credential verifies the signature.
    Credentials must be unique across *all* configured peers: even a peer that
    is not allowed to print could impersonate an allowed peer if both shared the
    same secret.
    """
    candidates: list[tuple[str, str]] = []
    token_to_peers: dict[str, list[str]] = {}
    peers = store.list_peers()
    peer_tokens: dict[str, str] = {}
    for peer in peers:
        peer_id = str(peer.get("peer_id") or "")
        if not peer_id:
            continue
        try:
            token = store.peer_token(peer_id)
        except Exception:
            # A broken encrypted credential must not become a partial identity
            # match. The peer simply cannot authenticate until repaired.
            continue
        if not token:
            continue
        peer_tokens[peer_id] = token
        token_to_peers.setdefault(token, []).append(peer_id)

    for peer in peers:
        if not _printing_receive_allowed(peer):
            continue
        peer_id = str(peer.get("peer_id") or "")
        token = peer_tokens.get(peer_id, "")
        if not token:
            continue
        proof = PrintRequestProof(peer_id=peer_id, **proof_values)
        expected = sign_request(proof, token)
        if hmac.compare_digest(signature, expected):
            candidates.append((peer_id, token))

    if len(candidates) != 1:
        raise ValueError("Peer-Druckidentität konnte nicht eindeutig authentisiert werden")
    peer_id, token = candidates[0]
    if len(token_to_peers.get(token, [])) != 1:
        raise ValueError("Peer-Druckcredential wird mehrfach verwendet; eindeutige Tokens erforderlich")
    return peer_id, PrintRequestProof(peer_id=peer_id, **proof_values)


def claim_nonce(root: str | Path, peer_id: str, nonce: str, *, now: int | None = None) -> bool:
    """Use the federation replay store to reject a signed print request twice."""
    current = int(time.time()) if now is None else int(now)
    scoped_nonce = f"print:{peer_id}:{nonce}"
    return FederationStore(root).claim_nonce(scoped_nonce, current + NONCE_TTL_SECONDS)
