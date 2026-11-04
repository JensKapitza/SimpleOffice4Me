"""Local public peer profile for discovery."""
from .federation_compatibility import local_application, local_features, local_protocol
from .federation_core import sanitize_peer_id
from .federation_discovery_endpoint import normalize_endpoint
from . import federation_local_identity as _identity
from .federation_identity import FederationIdentity
from .federation_lan_receive_state import LanReceiveState
from .build_master import LICENSE_MASTER_MODE, LICENSE_MASTER_URL


def local_peer_id():
    return sanitize_peer_id(_identity.peer_id())


def local_profile(root=None, fallback_base_url="", *, prefer_fallback=False):
    """Build the local public profile.

    prefer_fallback is reserved for an explicitly selected local endpoint,
    such as a LAN connect QR code. This keeps the normal published profile on
    its configured public URL while allowing one QR code per reachable LAN
    address without changing global configuration.
    """
    peer_id = local_peer_id()
    configured_url = _profile_public_url(root)
    fallback_url = str(fallback_base_url or "").strip()
    base_url = fallback_url if prefer_fallback and fallback_url else configured_url or fallback_url
    if not base_url:
        raise ValueError("public base URL is required for published discovery (admin identity section)")
    identity = FederationIdentity(root).public_identity() if root is not None else {
        "public_key": _identity.public_key()[:512],
        "fingerprint": _identity.fingerprint()[:256],
    }
    receive = LanReceiveState(root).status() if root is not None else {"active": False, "expires_at": 0}
    return {
        "peer_id": peer_id,
        "label": (_identity.label() or peer_id)[:160],
        "base_url": normalize_endpoint(base_url),
        "country": _profile_country(root),
        "fingerprint": identity["fingerprint"],
        "public_key": identity["public_key"],
        "master": {
            "is_master": bool(LICENSE_MASTER_MODE),
            "address": LICENSE_MASTER_URL if LICENSE_MASTER_MODE else "",
        },
        "application": local_application(),
        "federation": local_protocol(),
        "features": local_features(),
        "capabilities": {
            "discovery": True,
            "trust_claims": True,
            "rendezvous": True,
            "lan_discovery": True,
            "lan_receive_ready": bool(receive["active"]),
            "lan_receive_expires_at": int(receive["expires_at"]),
            "pairing_code_claim": True,
        },
    }


def _profile_country(root):
    if root is None:
        return _identity.country()[:2]
    from .settings_store import SettingsStore

    return SettingsStore(root).settings()["integrations"]["federation_profile_country"]

def _profile_public_url(root):
    if root is None:
        return _identity.public_url()
    from .settings_store import SettingsStore

    return SettingsStore(root).settings()["integrations"]["federation_public_url"]
