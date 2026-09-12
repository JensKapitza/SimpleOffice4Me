"""Peer profile validation for federation discovery."""
from .federation_core import sanitize_peer_id
from .federation_discovery_endpoint import normalize_endpoint
from .federation_identity import public_key_fingerprint


def peer_profile(data):
    if not isinstance(data, dict):
        raise ValueError("peer profile must be an object")
    country = str(data.get("country") or "").strip().upper()[:2]
    public_key = str(data.get("public_key") or "").strip()[:512]
    fingerprint = str(data.get("fingerprint") or "").strip()[:256]
    if public_key:
        derived = public_key_fingerprint(public_key)
        if fingerprint and fingerprint != derived:
            raise ValueError("peer fingerprint does not match public key")
        fingerprint = derived
    return {
        "peer_id": sanitize_peer_id(data.get("peer_id", "")),
        "label": str(data.get("label") or data.get("peer_id") or "")[:160],
        "base_url": normalize_endpoint(data.get("base_url", "")),
        "country": country,
        "fingerprint": fingerprint,
        "public_key": public_key,
        "capabilities": data.get("capabilities") if isinstance(data.get("capabilities"), dict) else {},
    }
