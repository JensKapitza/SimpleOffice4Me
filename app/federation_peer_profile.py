"""Peer profile validation for federation discovery."""
from .federation_compatibility import normalize_feature_versions, normalize_protocol_descriptor
from .federation_core import sanitize_peer_id
from .federation_discovery_endpoint import normalize_endpoint
from .federation_identity import public_key_fingerprint


def _application(data):
    value = data.get("application") if isinstance(data, dict) else None
    if not isinstance(value, dict):
        return {}
    name = str(value.get("name") or "").strip()[:80]
    version = str(value.get("version") or "").strip()[:64]
    return {"name": name, "version": version} if name else {}


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
        "application": _application(data),
        "federation": normalize_protocol_descriptor(data.get("federation")),
        "features": normalize_feature_versions(data.get("features")),
        "capabilities": data.get("capabilities") if isinstance(data.get("capabilities"), dict) else {},
    }
