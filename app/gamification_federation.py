"""Peer-bound authentication and minimal envelopes for gamification federation.

This module deliberately does not reuse the instance-wide federation bearer as
peer identity. Every request is signed with the configured peer-specific token,
uses a short timestamp window and a replay nonce, and is checked against that
peer's explicit ``gamification`` policy.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Mapping

from .federation_store import FederationStore

AUTH_VERSION = "SOFP-GAME-V1"
MAX_CLOCK_SKEW_SECONDS = 5 * 60
NONCE_TTL_SECONDS = 10 * 60
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
ALLOWED_ACTIONS = frozenset({"fetch_challenge", "fetch_preview", "submit_answer"})


@dataclass(frozen=True)
class GameRequestProof:
    peer_id: str
    timestamp: int
    nonce: str
    method: str
    path: str
    body_sha256: str
    action: str


def new_nonce() -> str:
    return secrets.token_urlsafe(24)


def body_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_request(proof: GameRequestProof) -> bytes:
    fields = (
        AUTH_VERSION,
        proof.peer_id,
        str(int(proof.timestamp)),
        proof.nonce,
        proof.method.upper(),
        proof.path,
        proof.body_sha256,
        proof.action,
    )
    if proof.action not in ALLOWED_ACTIONS:
        raise ValueError("invalid gamification federation action")
    if not SHA256_RE.fullmatch(proof.body_sha256):
        raise ValueError("invalid gamification body digest")
    if not NONCE_RE.fullmatch(proof.nonce):
        raise ValueError("invalid gamification nonce")
    if any("\n" in value or "\r" in value for value in fields):
        raise ValueError("invalid gamification signature input")
    return ("\n".join(fields) + "\n").encode("utf-8")


def sign_request(proof: GameRequestProof, token: str) -> str:
    secret = str(token or "")
    if not secret:
        raise ValueError("peer federation token is missing")
    return hmac.new(secret.encode("utf-8"), canonical_request(proof), hashlib.sha256).hexdigest()


def signed_headers(peer_id: str, token: str, *, method: str, path: str,
                   body: bytes = b"", action: str, now: int | None = None) -> dict[str, str]:
    proof = GameRequestProof(
        peer_id=str(peer_id).strip(),
        timestamp=int(time.time()) if now is None else int(now),
        nonce=new_nonce(),
        method=method.upper(),
        path=path,
        body_sha256=body_digest(body),
        action=action,
    )
    return {
        "X-SimpleOffice-Game-Peer": proof.peer_id,
        "X-SimpleOffice-Game-Timestamp": str(proof.timestamp),
        "X-SimpleOffice-Game-Nonce": proof.nonce,
        "X-SimpleOffice-Game-Body-SHA256": proof.body_sha256,
        "X-SimpleOffice-Game-Action": proof.action,
        "X-SimpleOffice-Game-Signature": sign_request(proof, token),
    }


def _peer_game_policy(peer: Mapping[str, Any]) -> dict[str, Any]:
    policy = peer.get("policy") if isinstance(peer, Mapping) else None
    game = policy.get("gamification") if isinstance(policy, Mapping) else None
    return dict(game) if isinstance(game, Mapping) else {}


def peer_allows(peer: Mapping[str, Any], permission: str, *, provider: str = "") -> bool:
    if not peer.get("enabled"):
        return False
    game = _peer_game_policy(peer)
    if game.get(permission) is not True:
        return False
    providers = game.get("providers")
    if provider and isinstance(providers, list):
        return provider in {str(value) for value in providers}
    return True


def parse_request_proof(headers: Mapping[str, Any], *, method: str, path: str,
                        body: bytes, now: int | None = None) -> tuple[GameRequestProof, str]:
    try:
        timestamp = int(str(headers.get("X-SimpleOffice-Game-Timestamp", "")))
    except ValueError as exc:
        raise ValueError("invalid gamification timestamp") from exc
    current = int(time.time()) if now is None else int(now)
    if abs(current - timestamp) > MAX_CLOCK_SKEW_SECONDS:
        raise ValueError("gamification federation signature expired")
    peer_id = str(headers.get("X-SimpleOffice-Game-Peer", "")).strip()
    nonce = str(headers.get("X-SimpleOffice-Game-Nonce", "")).strip()
    digest = str(headers.get("X-SimpleOffice-Game-Body-SHA256", "")).strip().casefold()
    action = str(headers.get("X-SimpleOffice-Game-Action", "")).strip()
    signature = str(headers.get("X-SimpleOffice-Game-Signature", "")).strip().casefold()
    if not peer_id or len(peer_id) > 200 or not NONCE_RE.fullmatch(nonce):
        raise ValueError("invalid gamification peer proof")
    if digest != body_digest(body) or not SHA256_RE.fullmatch(digest):
        raise ValueError("gamification request body mismatch")
    if not SHA256_RE.fullmatch(signature):
        raise ValueError("invalid gamification signature")
    proof = GameRequestProof(peer_id, timestamp, nonce, method.upper(), path, digest, action)
    canonical_request(proof)
    return proof, signature


def _token_is_unique(store: FederationStore, peer_id: str, token: str) -> bool:
    matches = 0
    for candidate in store.list_peers():
        candidate_id = str(candidate.get("peer_id") or "")
        if not candidate_id:
            continue
        try:
            candidate_token = store.peer_token(candidate_id)
        except Exception:
            continue
        if candidate_token and hmac.compare_digest(candidate_token, token):
            matches += 1
            if matches > 1:
                return False
    return matches == 1


def authenticate_peer(store: FederationStore, headers: Mapping[str, Any], *, method: str,
                      path: str, body: bytes, required_permission: str,
                      provider: str = "", claim_replay: bool = True) -> tuple[dict[str, Any], GameRequestProof]:
    proof, signature = parse_request_proof(headers, method=method, path=path, body=body)
    peer = store.get_peer(proof.peer_id)
    if peer is None or not peer_allows(peer, required_permission, provider=provider):
        raise ValueError("gamification federation policy denied")
    token = store.peer_token(proof.peer_id)
    if not _token_is_unique(store, proof.peer_id, token):
        raise ValueError("gamification peer credential is ambiguous")
    expected = sign_request(proof, token)
    if not hmac.compare_digest(signature, expected):
        raise ValueError("gamification peer authentication failed")
    if claim_replay:
        replay_key = f"game:{proof.peer_id}:{proof.nonce}"
        if not store.claim_nonce(replay_key, int(time.time()) + NONCE_TTL_SECONDS):
            raise ValueError("gamification federation request replayed")
    return peer, proof


def minimal_challenge_envelope(challenge: Mapping[str, Any]) -> dict[str, Any]:
    """Return only fields a remote player needs; never expose object_ref/path."""
    provider = str(challenge.get("provider", ""))
    payload = challenge.get("payload") if isinstance(challenge.get("payload"), Mapping) else {}
    safe_payload: dict[str, Any] = {}
    if provider == "images" and payload.get("preview") is True:
        safe_payload["preview"] = True
    elif provider in {"contacts", "documents"}:
        if payload.get("display_name"):
            safe_payload["display_name"] = str(payload["display_name"])[:200]
        if provider == "contacts" and payload.get("field"):
            safe_payload["field"] = str(payload["field"])[:80]
    return {
        "challenge_id": str(challenge.get("id", ""))[:80],
        "provider": provider,
        "kind": str(challenge.get("kind", ""))[:80],
        "prompt": str(challenge.get("prompt", ""))[:500],
        "answer_type": str(challenge.get("answer_type", ""))[:40],
        "payload": safe_payload,
    }


def json_body(value: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
