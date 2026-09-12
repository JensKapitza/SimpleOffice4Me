"""Peer profile validation for federation discovery."""
from .federation_core import sanitize_peer_id
from .federation_discovery_endpoint import normalize_endpoint


def peer_profile(data):
    if not isinstance(data, dict):
        raise ValueError("peer profile must be an object")
    country = str(data.get("country") or "").strip().upper()[:2]
    return {
        "peer_id": sanitize_peer_id(data.get("peer_id", "")),
        "label": str(data.get("label") or data.get("peer_id") or "")[:160],
        "base_url": normalize_endpoint(data.get("base_url", "")),
        "country": country,
        "fingerprint": str(data.get("fingerprint") or "")[:256],
        "capabilities": data.get("capabilities") if isinstance(data.get("capabilities"), dict) else {},
    }
