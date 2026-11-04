"""Local chat policy review hints (no network, stores nothing).

Flags enabled federation peers whose chat is fully off despite an
active peering, or whose text chat is on while every structured action
stays denied. Proposals are read-only with justifications and never
change data. Only peer labels and booleans are shown, never payloads.
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


def _on(value: Any) -> bool:
    """Interpret a policy flag strictly as opt-in."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip() in {"1", "True", "true", "yes", "on"}


def suggest_policy_review(
    peers: list[Any] | None,
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose chat policy follow-ups (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for peer in peers or []:
        if peer is None or isinstance(peer, (str, bytes)):
            continue
        if not _on(_get(peer, "enabled")):
            continue
        label = str(_get(peer, "label") or _get(peer, "peer_id") or "").strip()
        if not label:
            continue
        state = _get(peer, "chat_policy") or {}
        send = _on(_get(state, "send"))
        receive = _on(_get(state, "receive"))
        actions = _get(state, "actions") or {}
        action_on = False
        if isinstance(actions, dict):
            for entry in actions.values():
                if _on(_get(entry, "send")) or _on(_get(entry, "receive")):
                    action_on = True
                    break
        if not send and not receive:
            ranked.append(
                (
                    (0, label.casefold(), ""),
                    {
                        "reason": (
                            f"Partner „{label}“ ist aktiv, aber Chat "
                            "komplett deaktiviert — Absicht oder vergessen?"
                        ),
                    },
                )
            )
        elif not action_on:
            ranked.append(
                (
                    (1, label.casefold(), ""),
                    {
                        "reason": (
                            f"Partner „{label}“ erlaubt nur Text-Chat — "
                            "ggf. Kontakt, Umfrage oder Anfrage freischalten?"
                        ),
                    }
                )
            )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[:limit]]
