"""Local federation setup hints (no network, stores nothing).

Flags enabled peers without an access token and expired pairing codes
so connections can be completed through the normal federation routes.
Proposals are read-only with justifications and never change data. No
keys, tokens, or codes are shown, only the fact that one is missing.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 5


def _label(peer: dict[str, Any]) -> str:
    """Return a display label without secrets (no I/O)."""
    return (
        str(peer.get("label", "") or "").strip()
        or str(peer.get("peer_id", "") or "").strip()
        or "unbekannt"
    )


def suggest_federation_setup(
    peers: list[dict[str, Any]],
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose unfinished peer setups (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for peer in peers or []:
        if not isinstance(peer, dict):
            continue
        if not bool(peer.get("enabled", False)):
            continue
        label = _label(peer)
        peer_id = str(peer.get("peer_id", "") or "")
        if not bool(peer.get("has_token", False)):
            ranked.append(
                (
                    (0, label.casefold()),
                    {
                        "peer_id": peer_id,
                        "reason": (
                            f"Peer „{label}“ ist aktiviert, hat aber kein "
                            "Zugangstoken — Token einrichten."
                        ),
                    },
                )
            )
            continue
        pairing = peer.get("pairing", {})
        status = str(pairing.get("status", "") or "") if isinstance(pairing, dict) else ""
        if status == "expired":
            ranked.append(
                (
                    (1, label.casefold()),
                    {
                        "peer_id": peer_id,
                        "reason": (
                            f"Pairing-Code für „{label}“ ist abgelaufen — "
                            "neuen Code erzeugen."
                        ),
                    },
                )
            )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[:limit]]
