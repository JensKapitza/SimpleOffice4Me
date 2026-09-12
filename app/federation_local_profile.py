"""Local public peer profile for discovery."""
import os
import socket

from .federation_core import sanitize_peer_id
from .federation_discovery_endpoint import normalize_endpoint


def local_peer_id():
    raw_id = os.environ.get("SIMPLEOFFICE_FEDERATION_PEER_ID", "").strip() or socket.gethostname()
    return sanitize_peer_id(raw_id)


def local_profile():
    peer_id = local_peer_id()
    base_url = os.environ.get("SIMPLEOFFICE_FEDERATION_PUBLIC_URL", "").strip()
    if not base_url:
        raise ValueError("SIMPLEOFFICE_FEDERATION_PUBLIC_URL is required for discovery")
    return {
        "peer_id": peer_id,
        "label": os.environ.get("SIMPLEOFFICE_FEDERATION_LABEL", peer_id).strip()[:160],
        "base_url": normalize_endpoint(base_url),
        "country": os.environ.get("SIMPLEOFFICE_FEDERATION_COUNTRY", "").strip().upper()[:2],
        "fingerprint": os.environ.get("SIMPLEOFFICE_FEDERATION_FINGERPRINT", "").strip()[:256],
        "capabilities": {"discovery": True, "trust_claims": True, "rendezvous": True},
    }
