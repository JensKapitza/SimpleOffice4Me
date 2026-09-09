"""Outgoing peer client for signed gamification federation requests."""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any

from .federation_core import sanitize_peer_id
from .federation_store import FederationStore
from .federation_worker import _request, validate_transient_target
from .gamification_federation import json_body, peer_allows, signed_headers

MAX_RESPONSE_BYTES = 256 * 1024


def _local_peer_id() -> str:
    configured = os.environ.get("SIMPLEOFFICE_FEDERATION_PEER_ID", "").strip()
    fallback = socket.gethostname().strip().casefold().replace(" ", "-")[:128]
    return sanitize_peer_id(configured or fallback)


def _peer(root: str | Path, peer_id: str, permission: str, provider: str = "") -> tuple[dict[str, Any], str, str]:
    store = FederationStore(root)
    peer = store.get_peer(peer_id)
    if peer is None or not peer_allows(peer, permission, provider=provider):
        raise ValueError("gamification peer policy denied")
    base_url = validate_transient_target(str(peer.get("base_url", "")))
    token = store.peer_token(peer_id)
    if not token:
        raise ValueError("gamification peer token missing")
    matches = 0
    for candidate in store.list_peers():
        candidate_id = str(candidate.get("peer_id") or "")
        if not candidate_id:
            continue
        try:
            if store.peer_token(candidate_id) == token:
                matches += 1
        except Exception:
            continue
    if matches != 1:
        raise ValueError("gamification peer credential is ambiguous")
    return peer, base_url, token


def _read_json(response) -> dict[str, Any]:
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("gamification federation response too large")
    if not raw:
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("gamification federation response is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("gamification federation response is not an object")
    return value


def fetch_next(root: str | Path, peer_id: str, session_id: str) -> dict[str, Any] | None:
    peer, base_url, token = _peer(root, peer_id, "receive_challenges")
    safe_session = urllib.parse.quote(str(session_id), safe="")
    path = f"/federation/v1/gamification/sessions/{safe_session}/next"
    headers = signed_headers(_local_peer_id(), token, method="GET", path=path, action="fetch_challenge")
    try:
        with _request(base_url + path, method="GET", headers=headers, timeout=20) as response:
            data = _read_json(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 204:
            return None
        raise
    if not data:
        return None
    challenge_id = str(data.get("challenge_id", ""))
    provider = str(data.get("provider", ""))
    if not challenge_id or len(challenge_id) > 80 or provider not in {"images", "documents", "contacts"}:
        raise ValueError("peer returned invalid gamification challenge")
    if not peer_allows(peer, "receive_challenges", provider=provider):
        raise ValueError("peer returned provider outside local policy")
    data.pop("preview_endpoint", None)
    data["peer_id"] = peer_id
    return data


def fetch_preview(root: str | Path, peer_id: str, challenge_id: str) -> bytes:
    _peer_info, base_url, token = _peer(root, peer_id, "preview_media", provider="images")
    safe_challenge = urllib.parse.quote(str(challenge_id), safe="")
    path = f"/federation/v1/gamification/challenges/{safe_challenge}/preview"
    headers = signed_headers(_local_peer_id(), token, method="GET", path=path, action="fetch_preview")
    with _request(base_url + path, method="GET", headers=headers, timeout=20) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("gamification preview response too large")
    return raw


def submit_answer(root: str | Path, peer_id: str, challenge_id: str, *, answer: Any = None,
                  action: str = "answer") -> dict[str, Any]:
    action = str(action).strip().casefold()
    if action not in {"answer", "unknown", "skip"}:
        raise ValueError("invalid gamification answer action")
    _peer_info, base_url, token = _peer(root, peer_id, "submit_answers")
    safe_challenge = urllib.parse.quote(str(challenge_id), safe="")
    path = f"/federation/v1/gamification/challenges/{safe_challenge}/answer"
    payload: dict[str, Any] = {"action": action}
    if action == "answer":
        if not isinstance(answer, str) or not answer.strip() or len(answer.strip()) > 500:
            raise ValueError("invalid gamification answer")
        payload["answer"] = answer.strip()
    body = json_body(payload)
    headers = signed_headers(_local_peer_id(), token, method="POST", path=path, body=body, action="submit_answer")
    headers["Content-Type"] = "application/json"
    with _request(base_url + path, method="POST", body=body, headers=headers, timeout=20) as response:
        return _read_json(response)
