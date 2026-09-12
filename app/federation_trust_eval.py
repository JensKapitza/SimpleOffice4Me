"""Evaluate peer recommendations without auto-granting trust."""
from .federation_trust_constants import LOCAL_PEER
from .federation_trust_store import FederationTrustStore


def recommendations(root, target_peer):
    trust = FederationTrustStore(root)
    rows = []
    with trust.store._db() as db:
        claims = db.execute(
            """SELECT * FROM federation_trust_edge
            WHERE target_peer=? AND source_peer<>? AND propagation IN ('RECOMMENDATION_ONLY','TRANSITIVE')
            ORDER BY updated_at DESC""",
            (target_peer, LOCAL_PEER),
        ).fetchall()
    for claim in claims:
        source = trust.get_trust(claim["source_peer"], LOCAL_PEER)
        if not source or source.get("trust_level") == "NONE":
            continue
        rows.append({
            "source_peer": claim["source_peer"],
            "target_peer": claim["target_peer"],
            "source_trust": source.get("trust_level"),
            "claim_trust": claim["trust_level"],
            "propagation": claim["propagation"],
            "max_hops": claim["max_hops"],
            "verification_state": claim["verification_state"],
        })
    return rows
