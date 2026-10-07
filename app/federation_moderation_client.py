"""Bounded, peer-authenticated blacklist sync and report submission."""
import json
from .federation_core import canonical_json
from .federation_local_profile import local_peer_id
from .federation_moderation import FederationModerationStore, MAX_SNAPSHOT_BYTES, peer_id, reason_text
from .federation_peer_auth import headers as peer_headers
from .federation_store import FederationStore
from .federation_worker import _request


def _call(root, receiver, path, payload=None):
    store = FederationStore(root)
    peer = store.get_peer(peer_id(receiver))
    if not peer or not peer["enabled"]:
        raise ValueError("moderation peer is not enabled")
    token = store.peer_token(receiver)
    if not token:
        raise ValueError("moderation requires a peer-specific token")
    method = "GET" if payload is None else "POST"
    body = b"" if payload is None else canonical_json(payload)
    headers = peer_headers(local_peer_id(), token, method, path, body)
    if payload is not None:
        headers["Content-Type"] = "application/json"
    with _request(peer["base_url"] + path, method=method, body=body if payload is not None else None,
                  headers=headers, timeout=10) as response:
        raw = response.read(MAX_SNAPSHOT_BYTES + 1)
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise ValueError("moderation response too large")
    result = json.loads(raw.decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("invalid moderation response")
    return result


def sync_blacklist(root, source):
    moderation = FederationModerationStore(root)
    if source not in {row["peer_id"] for row in moderation.sources()}:
        raise ValueError("blacklist source is not subscribed")
    return moderation.import_snapshot(source, _call(root, source, "/federation/v1/moderation/blacklist"))


def send_report(root, receiver, target, reason):
    return _call(root, receiver, "/federation/v1/moderation/reports",
                 {"peer_id": peer_id(target), "reason": reason_text(reason)})
