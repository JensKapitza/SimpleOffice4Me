"""Local search index hints (no network, stores nothing).

Flags a running background scan, a reported scan error, or an index
that has not been updated for a while. Proposals are read-only with
justifications and never change data. Only the scan state and its
timestamp are shown, never document contents.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

MAX_ITEMS = 3
STALE_AFTER_DAYS = 7


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


def _day(raw: Any) -> str:
    """Return a YYYY-MM-DD key; empty when unparseable."""
    text = str(raw or "").strip()[:10]
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        if text.replace("-", "").isdigit():
            return text
    return ""


def suggest_index_review(
    scan_status: Any,
    today: str = "",
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose index follow-ups (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    current = (str(today or "").strip()[:10]) or date.today().isoformat()
    if not _day(current):
        current = date.today().isoformat()
    hints: list[dict[str, Any]] = []
    state = str(_get(scan_status, "state") or "").strip().lower()
    if state == "running":
        hints.append(
            {
                "reason": (
                    "Die Erfassung läuft noch im Hintergrund — "
                    "Trefferliste ggf. nach dem Abschluss erneut laden."
                ),
            }
        )
    elif state == "error":
        hints.append(
            {
                "reason": (
                    "Die letzte Erfassung meldet einen Fehler — "
                    "Status prüfen und Erfassung ggf. erneut anstoßen."
                ),
            }
        )
    else:
        updated = _day(_get(scan_status, "updated_at"))
        if not updated:
            hints.append(
                {
                    "reason": (
                        "Kein Erfassungszeitpunkt bekannt — "
                        "ggf. einmalig erfassen lassen."
                    ),
                }
            )
        else:
            stale_since = (
                date.fromisoformat(current) - timedelta(days=STALE_AFTER_DAYS)
            ).isoformat()
            if updated < stale_since:
                hints.append(
                    {
                        "reason": (
                            f"Index seit {updated} nicht aktualisiert — "
                            "ggf. Erfassung prüfen."
                        ),
                    }
                )
    return hints[:limit]
