"""Find active federation peers without a configured access token (read-only)."""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 5


def suggest_federation_setup(
    peers: list[dict[str, Any]],
    max_items: int = MAX_ITEMS,
) -> list[dict[str, str]]:
    """Report missing peer tokens, independent of optional pairing code status."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    hints: list[dict[str, str]] = []
    for peer in peers or []:
        if not isinstance(peer, dict) or not peer.get("enabled") or peer.get("has_token"):
            continue
        peer_id = str(peer.get("peer_id") or "").strip()
        if peer_id:
            label = str(peer.get("label") or "").strip() or peer_id
            hints.append({"peer_id": peer_id, "label": label})
    hints.sort(key=lambda hint: hint["label"].casefold())
    return hints[:limit]
