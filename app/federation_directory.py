"""Directory projection for explicitly published federation peers."""
from .federation_directory_store import FederationDirectoryStore
from .federation_trust_store import FederationTrustStore


def directory_profiles(root, country=""):
    trust = FederationTrustStore(root)
    visible = FederationDirectoryStore(root).peer_ids()
    identities = trust.list_identities(country)
    result = []
    for identity in identities:
        if identity["peer_id"] not in visible:
            continue
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
