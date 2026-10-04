"""Deny shared-token bypass when a federation blacklist is active."""
from .federation_peer_auth import authenticate
from .federation_store import FederationStore


def legacy_peer_allowed(root, request):
    if not FederationStore(root).banned_peer_ids():
        return True
    # An unsigned peer ID never establishes identity. Legacy shared credentials
    # cannot distinguish a banned peer, so require the existing peer HMAC proof.
    try:
        authenticate(root, request)
        return True
    except (TypeError, ValueError):
        return False
