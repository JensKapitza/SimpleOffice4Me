"""Off-site (OT) travel hints for the angel (deterministic, no model calls).

For the next upcoming off-site calendar event of a contact the angel
reports drive time, vehicle cost at the per-km rate, the time value at
the customer's gross hourly rate (when a rate is known from finalized
invoices), and a fair flat-rate suggestion.

Off-site detection is keyword-based over title, location, reason and
tag names ("OT", "vor Ort", "Auswärtstermin", ...). Distance comes from the
event's ``travel_km`` metadata (round trip, user-maintained); without
it there is no hint. The per-km default is the German 0,30 EUR
standard; the flat rate rounds the total up to 5-EUR steps.

Fail-closed: without contact manage rights, for unknown contacts, and
for unreadable or invalid data the hint is ``hidden``; without an
upcoming off-site event with distance it is ``none``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from ..business_document_generation import invoices
from ..calendar_metadata import MAX_TRAVEL_KM
from ..calendar_store import CalendarStore
from ..contact_store import ContactStore
from .case_links import BERLIN
from .contracts import INVOICE_TYPE, OtTravelHintState
from .credit_hints import format_eur, format_hours, latest_hourly_rate

MONEY = Decimal("0.01")
CURRENCY = "EUR"
DEFAULT_PER_KM = Decimal("0.30")
DEFAULT_AVG_KMH = Decimal("50")
FLAT_STEP = Decimal("5")

TAG_KEYWORDS = frozenset({
    "ot",
    "vor-ort",
    "vor ort",
    "auswärts",
    "auswärtstermin",
    "kundentermin",
    "baustelle",
    "außendienst",
    "aussendienst",
    "hausbesuch",
    "montage",
    "ortstermin",
})
TEXT_KEYWORDS = frozenset({
    "vor ort",
    "auswärtstermin",
    "kundentermin",
    "baustelle",
    "außendienst",
    "aussendienst",
    "hausbesuch",
    "montage",
    "ortstermin",
    "inbetriebnahme",
})
SHORT_KEYWORDS = frozenset({"ot"})

SKIP_STATUSES = frozenset({"cancelled", "deleted", "moved"})


def _words(text: str) -> set[str]:
    """Lowercased words of free text (splits on non-letters)."""
    word = ""
    found: set[str] = set()
    for char in text.casefold():
        if char.isalpha():
            word += char
        elif word:
            found.add(word)
            word = ""
    if word:
        found.add(word)
    return found


def is_offsite_event(event: Any) -> bool:
    """Detect off-site events by tags, title and location (no model)."""
    if not isinstance(event, dict):
        return False
    tags = event.get("tags")
    if isinstance(tags, list):
        for tag in tags:
            name = tag.get("name") if isinstance(tag, dict) else tag
            if str(name or "").strip().casefold() in TAG_KEYWORDS:
                return True
    haystack = " ".join(
        str(event.get(field) or "") for field in ("title", "location", "reason")
    ).casefold()
    if any(keyword in haystack for keyword in TEXT_KEYWORDS):
        return True
    return bool(_words(haystack) & SHORT_KEYWORDS)


def travel_km_from_event(event: Any) -> Decimal | None:
    """Round-trip distance of an event (None when missing or invalid)."""
    if not isinstance(event, dict):
        return None
    try:
        parsed = Decimal(str(event.get("travel_km") or "").strip().replace(",", "."))
    except (InvalidOperation, ValueError, AttributeError):
        return None
    if not parsed.is_finite() or parsed <= 0 or parsed > MAX_TRAVEL_KM:
        return None
    return parsed


def travel_cost(
    km: Decimal,
    *,
    per_km: Decimal = DEFAULT_PER_KM,
    hourly_gross: Decimal | None = None,
    avg_kmh: Decimal = DEFAULT_AVG_KMH,
) -> dict[str, Any]:
    """Compute drive time, vehicle cost, time value and flat rate."""
    if not isinstance(km, Decimal) or not km.is_finite() or km <= 0:
        raise ValueError("distance must be a positive number of kilometers")
    if not isinstance(per_km, Decimal) or not per_km.is_finite() or per_km <= 0:
        raise ValueError("per-km rate must be positive")
    if not isinstance(avg_kmh, Decimal) or not avg_kmh.is_finite() or avg_kmh <= 0:
        raise ValueError("average speed must be positive")
    if hourly_gross is not None and (
        not isinstance(hourly_gross, Decimal)
        or not hourly_gross.is_finite()
        or hourly_gross <= 0
    ):
        raise ValueError("hourly rate must be positive")
    drive_hours = km / avg_kmh
    vehicle = (km * per_km).quantize(MONEY, rounding=ROUND_HALF_UP)
    cost: dict[str, Any] = {
        "km": f"{km.normalize():f}",
        "km_display": f"{f'{km.normalize():f}'.replace('.', ',')} km",
        "drive_hours": f"{drive_hours.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}",
        "drive_display": format_hours(drive_hours),
        "vehicle": f"{vehicle:.2f}",
        "vehicle_display": format_eur(vehicle, CURRENCY),
        "has_time_value": False,
    }
    total = vehicle
    if hourly_gross is not None:
        time_value = (drive_hours * hourly_gross).quantize(MONEY, rounding=ROUND_HALF_UP)
        total = vehicle + time_value
        cost["has_time_value"] = True
        cost["time_value"] = f"{time_value:.2f}"
        cost["time_display"] = format_eur(time_value, CURRENCY)
    flat = max(
        FLAT_STEP,
        (total / FLAT_STEP).to_integral_value(rounding=ROUND_CEILING) * FLAT_STEP,
    )
    cost["total"] = f"{total:.2f}"
    cost["total_display"] = format_eur(total, CURRENCY)
    cost["flat"] = f"{flat:.2f}"
    cost["flat_display"] = format_eur(flat, CURRENCY)
    return cost


def next_offsite_event(
    events: Any, contact_id: str, today: str
) -> dict[str, Any] | None:
    """Nearest upcoming off-site event of a contact with distance."""
    if not isinstance(events, list):
        return None
    candidates: list[tuple[str, dict[str, Any]]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("contact_id") or "") != contact_id:
            continue
        if str(event.get("status") or "active") in SKIP_STATUSES:
            continue
        if travel_km_from_event(event) is None:
            continue
        if not is_offsite_event(event):
            continue
        start = str(event.get("start") or "")
        if start[:10] < today:
            continue
        candidates.append((start, event))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def ot_travel_hint_for_contact(
    root: str | Path,
    actor: str,
    contact_id: str,
    *,
    rows: list[dict[str, Any]] | None = None,
    today: str | None = None,
) -> OtTravelHintState:
    """Build the OT travel hint for one contact (see module docstring)."""
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("actor is required")
    if not isinstance(contact_id, str) or not contact_id.strip():
        raise ValueError("contact reference is required")
    try:
        contact = ContactStore(root).get(contact_id, actor)
        if not ContactStore(root).can_manage_contact(contact, actor):
            return {"state": "hidden", "reason": "no_manage_rights"}
    except (PermissionError, ValueError, KeyError, OSError):
        return {"state": "hidden", "reason": "contact_unreadable"}
    day = today or datetime.now(BERLIN).date().isoformat()
    try:
        events = CalendarStore(root).events(actor)
    except (OSError, ValueError):
        return {"state": "hidden", "reason": "calendar_unreadable"}
    event = next_offsite_event(events, contact_id, day)
    if event is None:
        return {"state": "none"}
    km = travel_km_from_event(event)
    if km is None:  # pragma: no cover - filtered above, kept for safety
        return {"state": "none"}
    rate: dict[str, Any] | None = None
    try:
        scanned = rows if rows is not None else invoices(Path(root))
        rate = latest_hourly_rate(
            [row for row in scanned if isinstance(row, dict)],
            contact_id,
            CURRENCY,
        )
    except (OSError, ValueError, AttributeError, TypeError):
        rate = None
    try:
        cost = travel_cost(
            km, hourly_gross=rate["rate_gross"] if rate is not None else None
        )
    except (ValueError, ArithmeticError):
        return {"state": "hidden", "reason": "travel_uncomputable"}
    hint: OtTravelHintState = {
        "state": "ready",
        "contact_id": contact_id,
        "currency": CURRENCY,
        "event_id": str(event.get("event_id", "")),
        "event_title": str(event.get("title", "")),
        "event_start": str(event.get("start", ""))[:10],
        **cost,
    }
    if rate is not None:
        gross_rate = rate["rate_gross"]
        label = rate["invoice_number"] or rate["invoice_id"][:8]
        rate_source = f"Rechnung {label}"
        if rate["issue_date"]:
            rate_source += f" vom {rate['issue_date']}"
        hint["rate_gross"] = f"{gross_rate:.2f}"
        hint["rate_display"] = format_eur(gross_rate, CURRENCY)
        hint["rate_basis"] = "brutto"
        hint["rate_source"] = rate_source
        hint["evidence"] = {
            "type": INVOICE_TYPE,
            "id": rate["invoice_id"],
            "url": rate["url"],
        }
    return hint


def card_ot_travel_hint(
    root: str | Path,
    actor: str,
    contact_id: str,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Ready-state travel dict for dashboard cards (None when nothing to show)."""
    if not contact_id:
        return None
    try:
        state = ot_travel_hint_for_contact(root, actor, contact_id, rows=rows)
    except (PermissionError, ValueError, OSError, KeyError, AttributeError, TypeError):
        return None
    return dict(state) if state.get("state") == "ready" else None
