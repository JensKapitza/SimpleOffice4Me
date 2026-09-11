"""Peer-bound HMAC authentication for SimpleOffice chat federation."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass

from .federation_core import sanitize_peer_id
from .federation_store import FederationStore

AUTH_VERSION = "SOFP-CHAT-V1"
MAX_CLOCK_SKEW_SECONDS = 5 * 60
NONCE_TTL_SECONDS = 10 * 60
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

@dataclass(frozen=True)
class ChatRequestProof:
    peer_id: str
    timestamp: int
    nonce: str
    kind: str
    resource_id: str
    payload_sha256: str
    payload_size: int

def new_nonce() -> str:
    return secrets.token_urlsafe(24)

def canonical_request(proof: ChatRequestProof) -> bytes:
    fields = (AUTH_VERSION, sanitize_peer_id(proof.peer_id), str(int(proof.timestamp)), proof.nonce, proof.kind, proof.resource_id, proof.payload_sha256, str(int(proof.payload_size)))
    if any("\r" in str(item) or "\n" in str(item) for item in fields):
        raise ValueError("Chat-Signatur enthält unzulässige Zeilenumbrüche")
    return ("\n".join(fields) + "\n").encode("utf-8")

def sign_request(proof: ChatRequestProof, token: str) -> str:
    secret = str(token or "")
    if not secret:
        raise ValueError("Peer-spezifischer Federation-Token fehlt")
    return hmac.new(secret.encode("utf-8"), canonical_request(proof), hashlib.sha256).hexdigest()

def headers_for(proof: ChatRequestProof, token: str) -> dict[str, str]:
    return {"X-SimpleOffice-Chat-Peer-ID": proof.peer_id, "X-SimpleOffice-Chat-Timestamp": str(proof.timestamp), "X-SimpleOffice-Chat-Nonce": proof.nonce, "X-SimpleOffice-Chat-Kind": proof.kind, "X-SimpleOffice-Chat-Resource-ID": proof.resource_id, "X-SimpleOffice-Payload-SHA256": proof.payload_sha256, "X-SimpleOffice-Payload-Size": str(proof.payload_size), "X-SimpleOffice-Chat-Signature": sign_request(proof, token)}

def parse_proof(headers) -> tuple[ChatRequestProof, str]:
    try:
        timestamp = int(str(headers.get("X-SimpleOffice-Chat-Timestamp", "") or ""))
        payload_size = int(str(headers.get("X-SimpleOffice-Payload-Size", "") or ""))
    except ValueError as exc:
        raise ValueError("Ungültige numerische Chat-Signatur-Header") from exc
    peer_id = sanitize_peer_id(str(headers.get("X-SimpleOffice-Chat-Peer-ID", "") or ""))
    nonce = str(headers.get("X-SimpleOffice-Chat-Nonce", "") or "").strip()
    kind = str(headers.get("X-SimpleOffice-Chat-Kind", "") or "").strip().casefold()
    resource_id = str(headers.get("X-SimpleOffice-Chat-Resource-ID", "") or "").strip()[:160]
    digest = str(headers.get("X-SimpleOffice-Payload-SHA256", "") or "").strip().casefold()
    signature = str(headers.get("X-SimpleOffice-Chat-Signature", "") or "").strip().casefold()
    if not _NONCE_RE.fullmatch(nonce): raise ValueError("Ungültiger Chat-Nonce")
    if kind not in {"event", "attachment"}: raise ValueError("Ungültiger Chat-Request-Typ")
    if not resource_id or "\r" in resource_id or "\n" in resource_id: raise ValueError("Ungültige Chat-Ressourcen-ID")
    if payload_size < 0: raise ValueError("Ungültige Chat-Payload-Größe")
    if not _SHA256_RE.fullmatch(digest) or not _SHA256_RE.fullmatch(signature): raise ValueError("Ungültige Chat-Prüfsumme oder Signatur")
    if abs(int(time.time()) - timestamp) > MAX_CLOCK_SKEW_SECONDS: raise ValueError("Chat-Signatur ist abgelaufen oder noch nicht gültig")
    return ChatRequestProof(peer_id, timestamp, nonce, kind, resource_id, digest, payload_size), signature

def _receive_allowed(peer: dict) -> bool:
    policy = peer.get("policy") if isinstance(peer.get("policy"), dict) else {}
    chat = policy.get("chat") if isinstance(policy, dict) else None
    return bool(peer.get("enabled") and isinstance(chat, dict) and chat.get("receive") is True)

def identify_source_peer(store: FederationStore, proof: ChatRequestProof, signature: str) -> dict:
    peer = store.get_peer(proof.peer_id)
    if not peer or not _receive_allowed(peer): raise ValueError("Federation-Peer darf keine Chats senden")
    token = store.peer_token(proof.peer_id)
    if not hmac.compare_digest(sign_request(proof, token), signature): raise ValueError("Chat-Signatur ist ungültig")
    duplicates = []
    for configured in store.list_peers():
        peer_id = str(configured.get("peer_id") or "")
        if not peer_id: continue
        try: candidate = store.peer_token(peer_id)
        except Exception: continue
        if candidate and hmac.compare_digest(candidate, token): duplicates.append(peer_id)
    if len(duplicates) != 1: raise ValueError("Federation-Token wird mehrfach verwendet; eindeutige Tokens erforderlich")
    return peer

def authenticate_request(store: FederationStore, headers, payload: bytes) -> tuple[dict, ChatRequestProof]:
    proof, signature = parse_proof(headers)
    if proof.payload_size != len(payload) or hashlib.sha256(payload).hexdigest() != proof.payload_sha256:
        raise ValueError("Chat-Payload stimmt nicht mit der Signatur überein")
    peer = identify_source_peer(store, proof, signature)
    if not store.claim_nonce(f"chat:{proof.peer_id}:{proof.nonce}", int(time.time()) + NONCE_TTL_SECONDS):
        raise ValueError("Chat-Request wurde bereits verarbeitet")
    return peer, proof
