"""Local stale channel hints (no network, stores nothing).

Flags measurement channels with no samples at all or with a latest
sample older than a threshold, so a stuck sensor or source can be
checked through the normal datalogger routes. Proposals are read-only
with justifications and never change data.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

MAX_ITEMS = 5
STALE_DAYS = 7


def _day(raw: object) -> str:
    """Return a YYYY-MM-DD key from a timestamp; empty when unparseable."""
    text = str(raw or "").strip()[:10]
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        if text.replace("-", "").isdigit():
            return text
    return ""


def suggest_stale_channels(
    series: list[dict[str, Any]],
    today: str = "",
    stale_days: int = STALE_DAYS,
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose channels without recent samples (never writes)."""
    current = (str(today or "").strip()[:10]) or date.today().isoformat()
    if _day(current) != current:
        current = date.today().isoformat()
    try:
        cutoff = (
            date.fromisoformat(current) - timedelta(days=max(0, int(stale_days)))
        ).isoformat()
    except (TypeError, ValueError):
        cutoff = current
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for row in series or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "") or "").strip() or "ohne Namen"
        samples = row.get("samples", [])
        latest = ""
        if isinstance(samples, list):
            days = [_day(item.get("measured_at", "")) for item in samples
                    if isinstance(item, dict)]
            days = [day for day in days if day]
            if days:
                latest = max(days)
        if latest and latest >= cutoff:
            continue
        if not latest:
            reason = (
                f"Kanal „{name}“ hat noch keine Messwerte — "
                "Quelle prüfen oder ersten Messwert abwarten."
            )
        else:
            reason = (
                f"Kanal „{name}“ meldet seit {latest} nichts — "
                "Sensor bzw. Quelle prüfen."
            )
        ranked.append(
            (
                (latest or "0000-00-00", name.casefold()),
                {
                    "channel_id": str(row.get("id", "") or ""),
                    "name": name,
                    "latest": latest,
                    "reason": reason,
                },
            )
        )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[: max(0, int(max_items))]]
