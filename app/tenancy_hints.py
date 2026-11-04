"""Local tenancy expiry hints (no network, stores nothing).

Flags tenancies that already ended or end within 30 days so follow-up
(renewal, new tenant, settlement) can be arranged through the normal
rental routes. Proposals are read-only with justifications and never
change data.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

MAX_ITEMS = 5
EXPIRY_WINDOW_DAYS = 30


def _day(raw: object) -> str:
    """Return a sortable YYYY-MM-DD key; empty when unparseable."""
    text = str(raw or "").strip()[:10]
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        if text.replace("-", "").isdigit():
            return text
    return ""


def suggest_tenancy_renewals(
    groups: list[dict[str, Any]],
    today: str = "",
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose tenancies ending soon or already ended (never writes)."""
    current = (str(today or "").strip()[:10]) or date.today().isoformat()
    if _day(current) != current:
        current = date.today().isoformat()
    horizon = (date.fromisoformat(current) + timedelta(days=EXPIRY_WINDOW_DAYS)).isoformat()
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for group in groups or []:
        if not isinstance(group, dict):
            continue
        group_name = str(group.get("name", "") or "").strip() or "ohne Gruppe"
        for unit in group.get("units", []) or []:
            if not isinstance(unit, dict):
                continue
            for tenancy in unit.get("tenancies", []) or []:
                if not isinstance(tenancy, dict):
                    continue
                ends_on = _day(tenancy.get("ends_on", ""))
                if not ends_on or ends_on > horizon:
                    continue
                contact = str(tenancy.get("contact_name", "") or "").strip() or "unbekannt"
                if ends_on < current:
                    reason = (
                        f"Mietverhältnis ({contact}, {group_name}) seit {ends_on} "
                        "beendet — Nachfolge oder Abrechnung klären."
                    )
                else:
                    reason = (
                        f"Mietverhältnis ({contact}, {group_name}) endet am {ends_on} — "
                        "Verlängerung oder Nachfolge jetzt klären."
                    )
                ranked.append(
                    (
                        (ends_on, contact.casefold()),
                        {
                            "contact": contact,
                            "group": group_name,
                            "ends_on": ends_on,
                            "ended": ends_on < current,
                            "reason": reason,
                        },
                    )
                )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[: max(0, int(max_items))]]
