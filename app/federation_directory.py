"""Directory projection for remembered federation peers."""
from .federation_trust_store import FederationTrustStore


def directory_profiles(root, country=""):
    trust = FederationTrustStore(root)
    identities = trust.list_identities(country)
    result = []
    for identity in identities:
        peer = trust.store.get_peer(identity["peer_id"])
        if not peer:
            continue
        result.append({
            "peer_id": peer["peer_id"],
            "label": peer.get("label") or peer["peer_id"],
            "base_url": peer.get("base_url") or "",
            "country": identity.get("country") or "",
            "fingerprint": identity.get("fingerprint") or "",
            "capabilities": {},
        })
    return result
