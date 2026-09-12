"""Import remote trust hints without granting local trust."""
from .federation_attestations import FederationAttestationStore
from .federation_store import FederationStore
from .federation_trust_store import FederationTrustStore
from .federation_worker import _json_request


def sync_claims(root, peer_id):
    peers = FederationStore(root)
    peer = peers.get_peer(peer_id)
    if not peer or not peer.get("enabled"):
        raise ValueError("unknown or disabled federation peer")
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

    attestations = FederationAttestationStore(root)
    verified_attestations = 0
    invalid_attestations = 0
    for item in data.get("attestations") or []:
        if not isinstance(item, dict):
            invalid_attestations += 1
            continue
        verifier = str(item.get("verifier_peer_id") or "")
        identity = trust.identity(verifier) if verifier else None
        public_key = str((identity or {}).get("public_key") or "")
        try:
            attestations.save_verified(item, public_key)
            verified_attestations += 1
        except ValueError:
            invalid_attestations += 1
    return {
        "peer_id": peer_id,
        "imported": imported,
        "verified_attestations": verified_attestations,
        "invalid_attestations": invalid_attestations,
    }
