"""Friend stock check over locally shared federation indexes (Bueroengel).

Friend = peer with an explicit NORMAL/HIGH trust edge AND an enabled
peer record AND a local documents-receive opt-in (both policy shapes:
``policy.documents.receive`` and ``policy.resources.documents.receive``,
mirroring resource_registry). Trust alone grants no data rights (see
docs/FEDERATION_DISCOVERY_TRUST.md: permissions stay separately
released), so the receive flag is the operative sharing consent. The
check reads only the local catalog copy for those friends and
exact-matches the normalized identifier against shared
paths/tags/document ids. No network, no content writes (the catalog
lazily keeps its own sync-state row), no data from or to
non-opt-in peers. Friends without a valid index are listed with
state not_synced/error and available:false — never as "no hit".
"""

from __future__ import annotations

import re
from typing import Any

from ..federation_catalog import FederationCatalog
from ..federation_store import FederationStore
from ..federation_trust_constants import LOCAL_PEER
from ..federation_trust_store import FederationTrustStore
from ..inventory import normalize_isbn
from .inventory_match import (
    IDENTIFIER_MAX_LENGTH,
    EmptyIdentifierError,
    IdentifierTooLongError,
    MalformedGtinError,
    clean_identifier,
    normalize_gtin,
)

FRIEND_TRUST_LEVELS = frozenset({"NORMAL", "HIGH"})
MAX_MATCHES_PER_FRIEND = 5
MAX_FRIENDS_CHECKED = 50
INDEX_OK = "ok"
INDEX_NOT_SYNCED = "not_synced"
INDEX_ERROR = "error"


def _documents_receive_opt_in(peer: dict[str, Any]) -> bool:
    """Return True only for an explicit local documents-receive flag.

    Accepts both stored policy shapes (``documents`` from the admin
    form and ``resources.documents`` from the phase-2 flow), mirroring
    resource_registry._document_policy. Anything but literal True
    means no sharing consent.
    """
    policy = peer.get("policy")
    if not isinstance(policy, dict):
        return False
    resources = policy.get("resources")
    if isinstance(resources, dict) and isinstance(resources.get("documents"), dict):
        documents: Any = resources["documents"]
    else:
        documents = policy.get("documents")
    return isinstance(documents, dict) and documents.get("receive") is True


def friend_peer_ids(*, root: object) -> list[str]:
    """Return friend peer ids: trust edge plus sharing consent.

    A peer counts only with an explicit NORMAL/HIGH trust edge, an
    existing enabled peer record, and a literal documents-receive
    opt-in. Trust without the receive flag (or vice versa) excludes
    the peer: trust alone grants no data rights.
    """
    peers = FederationStore(root)
    friends: list[str] = []
    for edge in FederationTrustStore(root).list_trust(LOCAL_PEER):
        if str(edge.get("trust_level", "NONE")).upper() not in FRIEND_TRUST_LEVELS:
            continue
        peer_id = str(edge.get("target_peer", "")).strip()
        if not peer_id:
            continue
        peer = peers.get_peer(peer_id)
        if not peer or not peer.get("enabled"):
            continue
        if not _documents_receive_opt_in(peer):
            continue
        friends.append(peer_id)
    return sorted(set(friends))[:MAX_FRIENDS_CHECKED]


def canonical_key(value: str) -> str:
    """Canonical ISBN/GTIN match key, else the exact stripped string."""
    try:
        return normalize_isbn(value)
    except ValueError:
        pass
    canonical_gtin = normalize_gtin(value)
    if canonical_gtin:
        return canonical_gtin
    return value


def _remote_candidates(entry: dict[str, Any]) -> list[tuple[str, str]]:
    path = str(entry.get("path", "") or "")
    base = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem = base.rsplit(".", 1)[0] if "." in base else base
    candidates = [
        ("id", str(entry.get("remote_document_id", "") or "")),
        ("path", path),
        ("path", base),
        ("path", stem),
    ]
    tags = entry.get("tags", [])
    if isinstance(tags, list):
        candidates.extend(("tag", str(tag)) for tag in tags)
    return [(field, clean_identifier(value)) for field, value in candidates]


def _classify_query(query: str) -> tuple[str, str]:
    try:
        return "isbn", normalize_isbn(query)
    except ValueError:
        pass
    canonical_gtin = normalize_gtin(query)
    if canonical_gtin:
        return "gtin", canonical_gtin
    return "barcode", query


def index_state(catalog: FederationCatalog, peer_id: str) -> str:
    """Return the sync trust state for one friend's local index.

    INDEX_OK only after a completed sync without a recorded error;
    anything else must not read as "no hit". Note: peer_state()
    lazily creates the catalog's own empty state row.
    """
    state = catalog.peer_state(peer_id)
    if str(state.get("last_index_error") or "").strip():
        return INDEX_ERROR
    if state.get("last_index_at"):
        return INDEX_OK
    return INDEX_NOT_SYNCED


def _like_needles(kind: str, query: str, key: str) -> list[str]:
    """SQL prefilters that cannot exclude exact/canonical stored forms.

    ISBN additionally sweeps unfiltered (""): stored paths may carry
    the digits with separators (dashes), which no single LIKE needle
    can cover. The final match stays exact either way.
    """
    if kind == "isbn":
        return [key, ""]
    if kind == "gtin":
        return [key.lstrip("0") or "0"]
    runs = re.findall(r"[A-Za-z0-9]+", query)
    return [max(runs, key=len) if runs else query[:200]]


def _candidates(
    catalog: FederationCatalog, peer_id: str, needles: list[str]
) -> list[dict[str, Any]]:
    """Union catalog rows over all needles, deduped by document id."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for needle in needles:
        for entry in catalog.remote_files(peer_id=peer_id, query=needle, limit=1000):
            ref = str(entry.get("remote_document_id", ""))
            if not ref or ref in seen:
                continue
            seen.add(ref)
            out.append(entry)
    return out


def peer_availability(*, root: object, actor: str, identifier: object) -> dict[str, Any]:
    """Check opt-in friends' shared catalogs for one identifier.

    Local: only friends' catalog rows are consulted, no network is
    used, no content is stored. Matches are exact on the normalized
    identifier; malformed GTIN-shaped input is rejected.
    friends_checked counts only friends with a valid index; friends
    without one are listed with state not_synced/error and
    available:false (no claim either way).
    """
    if not str(actor or "").strip():
        raise ValueError("actor is required")
    query = clean_identifier(identifier)
    if not query:
        raise EmptyIdentifierError("Bitte eine Kennung eingeben.")
    if len(query) > IDENTIFIER_MAX_LENGTH:
        raise IdentifierTooLongError("Kennung ist zu lang (maximal 120 Zeichen).")
    kind, key = _classify_query(query)
    if kind == "barcode":
        compact = query.replace(" ", "").replace("-", "")
        if len(compact) in (8, 12, 13, 14) and compact.isdigit():
            raise MalformedGtinError("Ungültige GTIN-Prüfziffer.")
    friends = friend_peer_ids(root=root)
    catalog = FederationCatalog(root)
    needles = _like_needles(kind, query, key)
    checked: list[dict[str, Any]] = []
    for peer_id in friends:
        state = index_state(catalog, peer_id)
        matches: list[dict[str, str]] = []
        if state == INDEX_OK:
            for entry in _candidates(catalog, peer_id, needles):
                if not entry.get("available"):
                    continue
                for field, candidate in _remote_candidates(entry):
                    if not candidate or canonical_key(candidate) != key:
                        continue
                    matches.append({"ref": str(entry.get("remote_document_id", "")), "field": field})
                    break
                if len(matches) >= MAX_MATCHES_PER_FRIEND:
                    break
        checked.append({
            "peer_id": peer_id,
            "available": bool(matches),
            "matches": matches,
            "state": state,
        })
    consulted = sum(1 for entry in checked if entry["state"] == INDEX_OK)
    return {
        "identifier": query,
        "kind": kind,
        "friends_checked": consulted,
        "friends_unavailable": len(checked) - consulted,
        "friends": checked,
    }
