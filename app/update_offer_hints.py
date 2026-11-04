"""Local update offer hints (no network, stores nothing).

Flags peer offers with a newer available release and staged releases
still waiting for installation. Proposals are read-only with
justifications and never change data. Only peer labels, statuses, and
counts already shown on the page are used, never binaries or digests.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 5


def _get(row: Any, name: str) -> Any:
    """Read a field from dicts, rows, or objects (no I/O)."""
    if isinstance(row, dict):
        return row.get(name)
    if hasattr(row, "_asdict"):
        try:
            return row._asdict().get(name)
        except (TypeError, AttributeError):
            return None
    if hasattr(row, "keys"):
        try:
            return row[name]
        except (KeyError, TypeError, IndexError):
            return None
    return getattr(row, name, None)


def _count(value: Any) -> int:
    """Coerce a count; unparseable counts as zero."""
    if isinstance(value, (list, tuple, set)):
        return len(value)
    try:
        return max(0, int(str(value or "").strip() or 0))
    except (TypeError, ValueError):
        return 0


def suggest_update_review(
    offers: list[Any] | None,
    staged: list[Any] | None,
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose update follow-ups (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    peers = sorted(
        {
            str(_get(offer, "peer_id") or "").strip()
            for offer in offers or []
            if offer is not None and not isinstance(offer, (str, bytes))
            and str(_get(offer, "status") or "").strip().lower() == "available"
        }
        - {""}
    )
    for peer in peers:
        ranked.append(
            (
                (0, peer.casefold(), ""),
                {
                    "reason": (
                        f"Peer „{peer}“ bietet eine neuere Version — "
                        "Angebot prüfen und ggf. stagen?"
                    ),
                },
            )
        )
    waiting = _count(staged)
    if waiting:
        ranked.append(
            (
                (1, "", ""),
                {
                    "reason": (
                        f"{waiting} "
                        f"{'gestagtes Release wartet' if waiting == 1 else 'gestagte Releases warten'} "
                        "auf Installation — installieren oder verwerfen?"
                    ),
                },
            )
        )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[:limit]]
