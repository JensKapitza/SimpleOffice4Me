"""Local rank motivation hints (no network, stores nothing).

Tells the current participant how much XP separates them from the next
rank up, or — when leading — how close the runner-up is. Proposals are
read-only with justifications and never change data. Only names and XP
already shown on the leaderboard are used, never anything private.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 3


def _get(row: Any, name: str) -> Any:
    """Read a field from dicts, rows, or objects (no I/O)."""
    if isinstance(row, dict):
        return row.get(name, "")
    if hasattr(row, "_asdict"):
        try:
            return row._asdict().get(name, "")
        except (TypeError, AttributeError):
            return ""
    if hasattr(row, "keys"):
        try:
            return row[name]
        except (KeyError, TypeError, IndexError):
            return ""
    return getattr(row, name, "")


def _xp(value: Any) -> int:
    """Coerce an XP value; unparseable counts as zero."""
    try:
        return max(0, int(str(value).strip() or 0))
    except (TypeError, ValueError):
        return 0


def suggest_rank_moves(
    rows: list[Any] | None,
    actor: str = "",
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose rank follow-ups for the actor (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    board = [
        (str(_get(row, "participant") or "").strip(), _xp(_get(row, "xp")))
        for row in rows or []
        if row is not None and not isinstance(row, (str, bytes))
    ]
    board = [(name, xp) for name, xp in board if name]
    me = str(actor or "").strip()
    if not board or not me:
        return []
    positions = [index for index, (name, _xp) in enumerate(board) if name == me]
    if not positions:
        return [
            {
                "reason": (
                    "Noch keine Punkte auf dem Board — die erste "
                    "bestätigte Verbesserung sammelt XP."
                ),
            }
        ][:limit]
    rank = positions[0]
    my_xp = board[rank][1]
    if rank == 0:
        if len(board) > 1:
            name, xp_value = board[1]
            if my_xp <= xp_value:
                return [
                    {
                        "reason": (
                            f"Kopf-an-Kopf mit „{name}“ bei {my_xp} XP — "
                            "die nächste bestätigte Verbesserung entscheidet."
                        ),
                    }
                ][:limit]
            gap = my_xp - xp_value
            return [
                {
                    "reason": (
                        f"Platz 1 mit {my_xp} XP — Verfolger „{name}“ "
                        f"liegt {gap} XP zurück."
                    ),
                }
            ][:limit]
        return [{"reason": "Platz 1 — weiter so."}][:limit]
    above_name, above_xp = board[rank - 1]
    if my_xp >= above_xp:
        return [
            {
                "reason": (
                    f"Gleichstand mit „{above_name}“ bei {my_xp} XP — "
                    "die nächste bestätigte Verbesserung entscheidet."
                ),
            }
        ][:limit]
    gap = above_xp - my_xp + 1
    return [
        {
            "reason": (
                f"Noch {gap} XP bis Rang {rank} "
                f"(vor dir: „{above_name}“ mit {above_xp} XP)."
            ),
        }
    ][:limit]
