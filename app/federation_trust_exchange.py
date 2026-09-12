"""Import remote trust hints without granting local trust."""
from .federation_store import FederationStore
from .federation_trust_store import FederationTrustStore
from .federation_worker import _json_request


def sync_claims(root, peer_id):
    peers = FederationStore(root)
    peer = peers.get_peer(peer_id)
    if not peer:
        raise ValueError("unknown federation peer")
    data = _json_request(
        peer["base_url"] + "/federation/v1/discovery/trust-claims",
        token=peers.peer_token(peer_id), timeout=10,
    )
    trust = FederationTrustStore(root)
    imported = 0
    for claim in data.get("claims") or []:
        if isinstance(claim, dict):
            trust.import_claim(peer_id, claim)
            imported += 1
    return {"peer_id": peer_id, "imported": imported}
