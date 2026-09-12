"""Peer-bound HMAC authentication for lightweight federation control calls."""
import hashlib
import hmac
import secrets
import time

from .federation_store import FederationStore

VERSION = "SOFP-PEER-V1"
MAX_SKEW = 300
NONCE_TTL = 600


def new_nonce():
    return secrets.token_urlsafe(24)


def _canonical(peer_id, method, path, timestamp, nonce, body_hash):
    fields = [VERSION, peer_id, method.upper(), path, str(int(timestamp)), nonce, body_hash]
    if any("\n" in value or "\r" in value for value in fields):
        raise ValueError("invalid peer authentication field")
    return ("\n".join(fields) + "\n").encode("utf-8")


def sign(peer_id, token, method, path, timestamp, nonce, body=b""):
    body_hash = hashlib.sha256(body or b"").hexdigest()
    return hmac.new(
        str(token).encode("utf-8"),
        _canonical(peer_id, method, path, timestamp, nonce, body_hash),
        hashlib.sha256,
    ).hexdigest()


def headers(peer_id, token, method, path, body=b""):
    timestamp = int(time.time())
    nonce = new_nonce()
    return {
        "X-SimpleOffice-Peer-ID": peer_id,
        "X-SimpleOffice-Peer-Timestamp": str(timestamp),
        "X-SimpleOffice-Peer-Nonce": nonce,
        "X-SimpleOffice-Peer-Signature": sign(peer_id, token, method, path, timestamp, nonce, body),
    }


def authenticate(root, request):
    peer_id = str(request.headers.get("X-SimpleOffice-Peer-ID", "")).strip()
    nonce = str(request.headers.get("X-SimpleOffice-Peer-Nonce", "")).strip()
    signature = str(request.headers.get("X-SimpleOffice-Peer-Signature", "")).strip().casefold()
    try:
        timestamp = int(request.headers.get("X-SimpleOffice-Peer-Timestamp", ""))
    except ValueError as exc:
        raise ValueError("invalid peer timestamp") from exc
    if not peer_id or not nonce or len(signature) != 64 or abs(int(time.time()) - timestamp) > MAX_SKEW:
        raise ValueError("invalid peer authentication")
    store = FederationStore(root)
    peer = store.get_peer(peer_id)
    if not peer or not peer.get("enabled"):
        raise ValueError("unknown or disabled peer")
    token = store.peer_token(peer_id)
    body = request.get_data(cache=True) or b""
    expected = sign(peer_id, token, request.method, request.path, timestamp, nonce, body)
    if not hmac.compare_digest(expected, signature):
        raise ValueError("invalid peer signature")
    if not store.claim_nonce(f"peer:{peer_id}:{nonce}", int(time.time()) + NONCE_TTL):
        raise ValueError("replayed peer request")
    return peer_id
