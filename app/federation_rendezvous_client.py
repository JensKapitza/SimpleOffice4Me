"""Client helpers for an authenticated shared federation rendezvous node."""
import json

from .federation_local_profile import local_peer_id
from .federation_peer_auth import headers as peer_headers
from .federation_store import FederationStore
from .federation_worker import _request


def _relay(root, relay_peer_id):
    store = FederationStore(root)
    relay = store.get_peer(relay_peer_id)
    if not relay or not relay.get("enabled"):
        raise ValueError("rendezvous peer is not enabled")
    return store, relay, store.peer_token(relay_peer_id), local_peer_id()


def send_signal(root, relay_peer_id, recipient_peer, payload, kind="connect", ttl_seconds=600):
    _store, relay, token, local_peer = _relay(root, relay_peer_id)
    path = "/federation/v1/discovery/signal"
    body = json.dumps({
        "recipient_peer": recipient_peer,
        "kind": kind,
        "payload": payload,
        "ttl_seconds": ttl_seconds,
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = peer_headers(local_peer, token, "POST", path, body)
    headers["Content-Type"] = "application/json"
    with _request(relay["base_url"] + path, method="POST", body=body, headers=headers, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def receive_signals(root, relay_peer_id):
    _store, relay, token, local_peer = _relay(root, relay_peer_id)
    path = "/federation/v1/discovery/signal"
    headers = peer_headers(local_peer, token, "GET", path)
    with _request(relay["base_url"] + path, method="GET", headers=headers, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data.get("messages", []) if isinstance(data, dict) else []
