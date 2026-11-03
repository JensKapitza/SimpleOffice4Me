"""Friend inventory check over locally synced inventory indexes.

Unlike the document-hint check in peer_stock, this answers whether an
opt-in friend actually holds an item with an exact ISBN/GTIN — backed
by a dedicated synced index that carries only canonical identifiers
and neutral display names. No prices, locations, notes, images,
document contents, or owner data are ever synced or consulted.

Both sides decide separately with an explicit default-deny flag:
friends need a trust edge, an enabled peer record, and a literal
``policy.inventory.receive is True``. Only locally synchronized rows
are read; this function performs no network access. ISBN/GTIN-only:
titles and plain barcodes are never matched.
"""
from __future__ import annotations

from typing import Any

from ..federation_catalog import FederationCatalog
from ..federation_store import FederationStore
from ..federation_trust_constants import LOCAL_PEER
from ..federation_trust_store import FederationTrustStore
from .inventory_match import (
    IDENTIFIER_MAX_LENGTH,
    EmptyIdentifierError,
    IdentifierTooLongError,
    MalformedGtinError,
    clean_identifier,
    classify_identifier,
)
from .peer_stock import INDEX_ERROR, INDEX_NOT_SYNCED, INDEX_OK, MAX_FRIENDS_CHECKED, MAX_MATCHES_PER_FRIEND

FRIEND_TRUST_LEVELS = frozenset({"NORMAL", "HIGH"})


def _inventory_receive_opt_in(peer: dict[str, Any]) -> bool:
    """Default deny: only a literal inventory-receive flag counts."""
    policy = peer.get("policy")
    if not isinstance(policy, dict):
        return False
    inventory = policy.get("inventory")
    return isinstance(inventory, dict) and inventory.get("receive") is True


def friend_inventory_peer_ids(*, root: object) -> list[str]:
    """Friend peer ids with trust edge, sharing consent, and a valid trust record.

    A peer counts only with an explicit NORMAL/HIGH trust edge, an
    existing enabled peer record, and a literal inventory-receive
    opt-in. Trust alone, a missing peer record, or a documents-only
    opt-in never qualify.
    """
    trust = FederationTrustStore(root).list_trust(source_peer=LOCAL_PEER)
    levels = {str(edge.get("target_peer", "")): str(edge.get("trust_level", "")) for edge in trust}
    peers = FederationStore(root)
    friends: list[str] = []
    for peer_id, level in levels.items():
        if level not in FRIEND_TRUST_LEVELS:
            continue
        peer = peers.get_peer(peer_id)
        if not peer or not peer.get("enabled"):
            continue
        if not _inventory_receive_opt_in(peer):
            continue
        friends.append(peer_id)
    return sorted(set(friends))[:MAX_FRIENDS_CHECKED]


def inventory_index_state(catalog: FederationCatalog, peer_id: str) -> str:
    """Trust state of one friend's local inventory index.

    INDEX_OK only after a completed sync without a recorded error;
    anything else must not read as "not owned".
    """
    state = catalog.inventory_state(peer_id)
    if str(state.get("last_index_error") or "").strip():
        return INDEX_ERROR
    if state.get("last_index_at"):
        return INDEX_OK
    return INDEX_NOT_SYNCED


def peer_inventory_availability(*, root: object, actor: str, identifier: object) -> dict[str, Any]:
    """Check opted-in friends' synced inventory indexes for one identifier.

    Exact ISBN/GTIN only: the canonical key must equal a synced row's
    identifier. Friends without a valid index are listed with state
    not_synced/error and available:false (no claim either way).
    """
    if not str(actor or "").strip():
        raise ValueError("actor is required")
    query = clean_identifier(identifier)
    if not query:
        raise EmptyIdentifierError("Bitte eine Kennung eingeben.")
    if len(query) > IDENTIFIER_MAX_LENGTH:
        raise IdentifierTooLongError("Kennung ist zu lang (maximal 120 Zeichen).")
    kind, key = classify_identifier(query)
    if kind == "barcode":
        compact = query.replace(" ", "").replace("-", "")
        if len(compact) in (8, 12, 13, 14) and compact.isdigit():
            raise MalformedGtinError("Ungültige GTIN-Prüfziffer.")
        raise MalformedGtinError("Nur ISBN/GTIN werden für den Inventarabgleich unterstützt.")
    catalog = FederationCatalog(root)
    checked: list[dict[str, Any]] = []
    for peer_id in friend_inventory_peer_ids(root=root):
        state = inventory_index_state(catalog, peer_id)
        matches: list[dict[str, str]] = []
        if state == INDEX_OK:
            for entry in catalog.remote_inventory_items(peer_id, key):
                if not entry.get("available"):
                    continue
                if str(entry.get("identifier", "")) != key:
                    continue
                matches.append({
                    "ref": str(entry.get("identifier", "")),
                    "field": "identifier",
                    "display_name": str(entry.get("display_name", "") or ""),
                })
                if len(matches) >= MAX_MATCHES_PER_FRIEND:
                    break
        checked.append({
            "peer_id": peer_id, "state": state,
            "available": bool(matches), "matches": matches,
        })
    consulted = sum(1 for entry in checked if entry["state"] == INDEX_OK)
    return {
        "identifier": query,
        "kind": kind,
        "friends_checked": consulted,
        "friends_unavailable": len(checked) - consulted,
        "friends": checked,
    }
