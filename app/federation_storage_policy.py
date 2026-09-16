"""Placement and disclosure policy for federation storage peers.

This module deliberately separates *where ciphertext may be stored* from *who
may decrypt it*. Public storage peers are treated as untrusted by default.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

TRUST_LEVELS = ("local", "trusted-lan", "trusted-vpn", "trusted-internet", "private", "public")
TRUST_RANK = {name: index for index, name in enumerate(TRUST_LEVELS)}


@dataclass(frozen=True)
class StoragePeer:
    peer_id: str
    trust: str = "public"
    priority: int = 0
    free_bytes: int = 0
    enabled: bool = True
    encrypted_storage_only: bool = True

    def __post_init__(self) -> None:
        if self.trust not in TRUST_RANK:
            raise ValueError("unsupported trust level")
        if not self.peer_id:
            raise ValueError("peer id required")


@dataclass(frozen=True)
class PlacementPolicy:
    """Policy for encrypted blob/shard placement.

    `public_max_shards_per_peer` limits exposure to one public peer. It does not
    claim to prevent colluding peers from exchanging their stored ciphertext.
    """

    name: str = "private-preferred"
    allow_public: bool = False
    minimum_copies: int = 2
    target_copies: int = 3
    preferred_trust: tuple[str, ...] = ("local", "trusted-lan", "trusted-vpn", "private")
    public_max_shards_per_peer: int = 1
    minimum_distinct_public_peers: int = 3
    public_metadata: bool = False
    public_plaintext_hash: bool = False
    public_key_envelopes: bool = False

    def __post_init__(self) -> None:
        if self.minimum_copies < 1 or self.target_copies < self.minimum_copies:
            raise ValueError("invalid replication target")
        if self.public_max_shards_per_peer < 1:
            raise ValueError("public shard limit must be positive")
        if self.minimum_distinct_public_peers < 1:
            raise ValueError("public peer minimum must be positive")
        if any(level not in TRUST_RANK for level in self.preferred_trust):
            raise ValueError("unsupported preferred trust level")

    def public_disclosure(self) -> dict[str, bool]:
        return {
            "metadata": self.public_metadata,
            "plaintext_hash": self.public_plaintext_hash,
            "key_envelopes": self.public_key_envelopes,
        }


def peer_score(peer: StoragePeer, policy: PlacementPolicy) -> tuple[int, int, int, str]:
    try:
        preference = policy.preferred_trust.index(peer.trust)
    except ValueError:
        preference = len(policy.preferred_trust) + TRUST_RANK[peer.trust]
    return (preference, -int(peer.priority), -max(0, int(peer.free_bytes)), peer.peer_id)


def eligible_peers(peers: Iterable[StoragePeer], policy: PlacementPolicy) -> list[StoragePeer]:
    result = []
    for peer in peers:
        if not peer.enabled:
            continue
        if peer.trust == "public" and not policy.allow_public:
            continue
        result.append(peer)
    return sorted(result, key=lambda peer: peer_score(peer, policy))


def place_replicas(peers: Iterable[StoragePeer], policy: PlacementPolicy) -> list[str]:
    """Select preferred peers for complete encrypted replicas."""
    return [peer.peer_id for peer in eligible_peers(peers, policy)[: policy.target_copies]]


def place_shards(
    shard_ids: Iterable[str], peers: Iterable[StoragePeer], policy: PlacementPolicy
) -> dict[str, list[str]]:
    """Spread opaque shard identifiers while limiting public concentration."""
    shards = list(dict.fromkeys(str(item) for item in shard_ids if item))
    candidates = eligible_peers(peers, policy)
    if not shards or not candidates:
        return {}
    public = [peer for peer in candidates if peer.trust == "public"]
    private = [peer for peer in candidates if peer.trust != "public"]
    if policy.allow_public and public and len(public) < policy.minimum_distinct_public_peers:
        # Public fallback is all-or-nothing when its diversity requirement cannot be met.
        public = []
    ordered = private + public
    assigned: dict[str, list[str]] = {peer.peer_id: [] for peer in ordered}
    public_counts = {peer.peer_id: 0 for peer in public}
    cursor = 0
    for shard_id in shards:
        placed = False
        for offset in range(len(ordered)):
            peer = ordered[(cursor + offset) % len(ordered)]
            if peer.trust == "public" and public_counts[peer.peer_id] >= policy.public_max_shards_per_peer:
                continue
            assigned[peer.peer_id].append(shard_id)
            if peer.trust == "public":
                public_counts[peer.peer_id] += 1
            cursor = (cursor + offset + 1) % len(ordered)
            placed = True
            break
        if not placed:
            raise ValueError("placement policy cannot place all shards")
    return {peer_id: values for peer_id, values in assigned.items() if values}


def public_storage_record(*, storage_id: str, size: int, cipher_hash: str) -> dict[str, Any]:
    """Return the maximum record an untrusted public peer needs to retain."""
    if not storage_id or not cipher_hash:
        raise ValueError("storage id and cipher hash required")
    return {"storage_id": storage_id, "size": max(0, int(size)), "cipher_hash": cipher_hash}
