"""Local public peer profile for discovery."""
import os
import socket

from .federation_core import sanitize_peer_id
from .federation_discovery_endpoint import normalize_endpoint
from .federation_identity import FederationIdentity
from .federation_lan_receive_state import LanReceiveState


def local_peer_id():
    raw_id = os.environ.get("SIMPLEOFFICE_FEDERATION_PEER_ID", "").strip() or socket.gethostname()
    return sanitize_peer_id(raw_id)


def local_profile(root=None, fallback_base_url="", *, prefer_fallback=False):
    """Build the local public profile.

    prefer_fallback is reserved for an explicitly selected local endpoint,
    such as a LAN connect QR code. This keeps the normal published profile on
    its configured public URL while allowing one QR code per reachable LAN
    address without changing global configuration.
    """
    peer_id = local_peer_id()
    configured_url = os.environ.get("SIMPLEOFFICE_FEDERATION_PUBLIC_URL", "").strip()
    fallback_url = str(fallback_base_url or "").strip()
    base_url = fallback_url if prefer_fallback and fallback_url else configured_url or fallback_url
    if not base_url:
        raise ValueError("SIMPLEOFFICE_FEDERATION_PUBLIC_URL is required for published discovery")
    identity = FederationIdentity(root).public_identity() if root is not None else {
        "public_key": os.environ.get("SIMPLEOFFICE_FEDERATION_PUBLIC_KEY", "").strip()[:512],
        "fingerprint": os.environ.get("SIMPLEOFFICE_FEDERATION_FINGERPRINT", "").strip()[:256],
    }
    receive = LanReceiveState(root).status() if root is not None else {"active": False, "expires_at": 0}
    return {
        "peer_id": peer_id,
        "label": os.environ.get("SIMPLEOFFICE_FEDERATION_LABEL", peer_id).strip()[:160],
        "base_url": normalize_endpoint(base_url),
        "country": os.environ.get("SIMPLEOFFICE_FEDERATION_COUNTRY", "").strip().upper()[:2],
        "fingerprint": identity["fingerprint"],
        "public_key": identity["public_key"],
        "capabilities": {
            "discovery": True,
            "trust_claims": True,
            "rendezvous": True,
            "lan_discovery": True,
            "lan_receive_ready": bool(receive["active"]),
            "lan_receive_expires_at": int(receive["expires_at"]),
        },
    }
