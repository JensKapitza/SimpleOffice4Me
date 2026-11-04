"""Local print job hints (no network, stores nothing).

Flags failed print jobs that can be retried and queued jobs that are
still waiting, so they can be handled through the normal printershare
routes. Proposals are read-only with justifications and never change
data. Only job metadata (printer, status) is shown, never payloads.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 5


def suggest_print_job_actions(
    jobs: list[dict[str, Any]],
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose failed/waiting print jobs (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        status = str(job.get("status", "") or "")
        if status not in {"failed", "queued"}:
            continue
        printer = str(job.get("printer_name", "") or "").strip() or "unbekannt"
        if status == "failed":
            reason = (
                f"Druckauftrag an „{printer}“ fehlgeschlagen — "
                "Fehler prüfen und bei vorhandener Kopie erneut drucken."
            )
        else:
            reason = (
                f"Druckauftrag an „{printer}“ wartet noch — "
                "Drucker und Warteschlange prüfen."
            )
        ranked.append(
            (
                (0 if status == "failed" else 1, printer.casefold()),
                {
                    "job_id": str(job.get("job_id", "") or ""),
                    "printer": printer,
                    "status": status,
                    "reason": reason,
                },
            )
        )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[:limit]]
