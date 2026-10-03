"""Deny shared-token bypass when a federation blacklist is active."""
from flask import g

from .federation_peer_auth import authenticate
from .federation_store import FederationStore


def legacy_peer_allowed(root, request):
    if not FederationStore(root).banned_peer_ids():
        return True
    # An unsigned peer ID never establishes identity. Legacy shared credentials
    # cannot distinguish a banned peer, so require the existing peer HMAC proof.
    if getattr(g, "moderation_authenticated_peer", None):
        return True
    try:
        g.moderation_authenticated_peer = authenticate(root, request)
        return True
    except (TypeError, ValueError):
        return False


def signed_peer_authorized(root, request):
    if not request.headers.get("X-SimpleOffice-Peer-Signature"):
        return False
    if getattr(g, "moderation_authenticated_peer", None):
        return True
    try:
        g.moderation_authenticated_peer = authenticate(root, request)
        return True
    except (TypeError, ValueError):
        return False
