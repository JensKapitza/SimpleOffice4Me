"""Invoice-position hints for the angel (deterministic, no model calls).

Groups finalized invoice lines of one contact by normalized description
and unit, then reports: repeated positions worth turning into catalog
articles (3+ invoices by default), price spread inside one position
("billed differently" — check the contract letter or customer note, or
prepare unification for the quarter change), and draft lines deviating
from the usual price. Article creation itself stays an explicit,
rights-checked POST on the editor panel.

Fail-closed: without contact manage rights, for unknown contacts, and
for unreadable data the hint is ``hidden``; without patterns it is
``none``. Normalization is deliberately conservative (case, spacing,
trailing punctuation only) — positions differing by numbers stay
separate rather than being merged wrongly.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..business_document_generation import INVOICE_UNITS, invoices
from ..contact_store import ContactStore
from ..object_store import ObjectStore
from .contracts import INVOICE_URL

MONEY = Decimal("0.01")
MIN_ARTICLE_INVOICES = 3
MAX_PANEL_ITEMS = 3


def _money(value: Any) -> Decimal | None:
    """Parse stored money defensively; garbage becomes None (never raises)."""
    try:
        parsed = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError, AttributeError):
        return None
    return parsed if parsed.is_finite() else None


def normalize_position(text: Any) -> str:
    """Conservative grouping key: case, spacing, trailing punctuation."""
    collapsed = " ".join(str(text or "").split())
    return collapsed.casefold().rstrip(".,;:!?").strip()


def format_eur(amount: Decimal, currency: str = "EUR") -> str:
    """Format money for German UI text ("1234.5" -> "1.234,50 EUR")."""
    quantized = amount.quantize(MONEY, rounding=ROUND_HALF_UP)
    grouped = f"{quantized:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{grouped} {currency}"


def unit_display(unit: str) -> str:
    """German unit label ("HUR" -> "Stunde", unknown codes pass through)."""
    entry = INVOICE_UNITS.get(str(unit or "").strip().upper())
    if isinstance(entry, dict) and entry.get("de"):
        return str(entry["de"])
    return str(unit or "").strip().upper() or "Position"


def next_quarter_start(today: date) -> date:
    """First day of the next calendar quarter (unification target)."""
    quarter = (today.month - 1) // 3
    if quarter >= 3:
        return date(today.year + 1, 1, 1)
    return date(today.year, (quarter + 1) * 3 + 1, 1)


def position_patterns(
    rows: list[dict[str, Any]], contact_id: str, currency: str
) -> list[dict[str, Any]]:
    """Group finalized lines of one contact by normalized description+unit."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("contact_id", "")) != contact_id:
            continue
        if str(row.get("currency", "EUR")).upper() != currency:
            continue
        if str(row.get("status", "")) in {"draft", "finalizing", ""}:
            continue
        lines = row.get("lines")
        if not isinstance(lines, list):
            continue
        invoice_id = str(row.get("invoice_id", ""))
        seen_in_row: set[tuple[str, str]] = set()
        for line in lines:
            if not isinstance(line, dict):
                continue
            key = (
                normalize_position(line.get("description")),
                str(line.get("unit", "")).strip().upper(),
            )
            if not key[0]:
                continue
            net = _money(line.get("net_unit_price"))
            vat = _money(line.get("vat_rate"))
            group = groups.setdefault(
                key,
                {
                    "key": key[0],
                    "unit": key[1],
                    "display": str(line.get("description", "")).strip(),
                    "invoice_ids": [],
                    "invoices": [],
                    "prices": [],
                },
            )
            if net is not None and net >= 0 and vat is not None and vat >= 0:
                group["prices"].append(
                    {"net": net, "vat": vat, "invoice_id": invoice_id}
                )
            if key not in seen_in_row:
                seen_in_row.add(key)
                group["invoice_ids"].append(invoice_id)
                group["invoices"].append(
                    {
                        "invoice_id": invoice_id,
                        "invoice_number": str(row.get("invoice_number", "")),
                        "issue_date": str(row.get("issue_date", "")),
                        "url": INVOICE_URL.format(invoice_id=quote(invoice_id, safe="")),
                    }
                )
    patterns = []
    for group in groups.values():
        prices = sorted(item["net"] for item in group["prices"])
        median = prices[len(prices) // 2] if prices else None
        patterns.append(
            {
                **group,
                "invoice_count": len(group["invoice_ids"]),
                "min_net": prices[0] if prices else None,
                "max_net": prices[-1] if prices else None,
                "median_net": median,
                "spread": bool(prices) and prices[0] != prices[-1],
            }
        )
    patterns.sort(key=lambda item: (-item["invoice_count"], item["display"]))
    return patterns


def _invoice_enabled(item: dict[str, Any]) -> bool:
    """True for active catalog objects usable on invoices."""
    invoice = item.get("invoice") if isinstance(item.get("invoice"), dict) else {}
    enabled = invoice.get("use_in_invoice") is True or str(
        invoice.get("use_in_invoice", "")
    ).strip().casefold() in {"1", "true", "yes", "on", "checked"}
    return bool(enabled) and str(item.get("status", "")) == "active"


def catalog_names(objects: list[dict[str, Any]]) -> set[str]:
    """Normalized names of invoice-enabled catalog objects."""
    names = set()
    for item in objects:
        if not isinstance(item, dict):
            continue
        if not _invoice_enabled(item):
            continue
        invoice = item.get("invoice") if isinstance(item.get("invoice"), dict) else {}
        for candidate in (item.get("name", ""), invoice.get("description", "")):
            normalized = normalize_position(candidate)
            if normalized:
                names.add(normalized)
    return names


def article_proposals(
    patterns: list[dict[str, Any]],
    existing: set[str],
    currency: str,
    min_invoices: int = MIN_ARTICLE_INVOICES,
) -> list[dict[str, Any]]:
    """Repeated positions missing from the catalog (most frequent first)."""
    proposals = []
    for pattern in patterns:
        if pattern["invoice_count"] < min_invoices:
            continue
        if pattern["key"] in existing:
            continue
        if pattern["median_net"] is None:
            continue
        vat_counts: dict[str, int] = {}
        for item in pattern["prices"]:
            key = str(item["vat"])
            vat_counts[key] = vat_counts.get(key, 0) + 1
        vats = sorted(vat_counts, key=lambda key: (-vat_counts[key], key))
        proposals.append(
            {
                "name": pattern["display"][:240],
                "unit": pattern["unit"],
                "unit_display": unit_display(pattern["unit"]),
                "net_price": f"{pattern['median_net']:.2f}",
                "net_display": format_eur(pattern["median_net"], currency),
                "vat_rate": vats[0],
                "invoice_count": pattern["invoice_count"],
            }
        )
    return proposals


def variance_hints(
    patterns: list[dict[str, Any]], currency: str, today: date
) -> list[dict[str, Any]]:
    """Positions billed at different net prices (with quarter target)."""
    target = next_quarter_start(today).strftime("%d.%m.%Y")
    hints = []
    for pattern in patterns:
        if not pattern["spread"] or pattern["min_net"] is None:
            continue
        assert pattern["max_net"] is not None
        hints.append(
            {
                "display": pattern["display"],
                "unit": pattern["unit"],
                "unit_display": unit_display(pattern["unit"]),
                "min_display": format_eur(pattern["min_net"], currency),
                "max_display": format_eur(pattern["max_net"], currency),
                "invoice_count": pattern["invoice_count"],
                "invoices": pattern["invoices"][:4],
                "quarter_target": target,
            }
        )
    return hints


def draft_flags(
    draft_lines: Any, patterns: list[dict[str, Any]], currency: str
) -> list[dict[str, Any]]:
    """Draft lines deviating from their usual net price."""
    if not isinstance(draft_lines, list):
        return []
    usual = {(item["key"], item["unit"]): item for item in patterns}
    flags = []
    for position, line in enumerate(draft_lines, start=1):
        if not isinstance(line, dict):
            continue
        key = (
            normalize_position(line.get("description")),
            str(line.get("unit", "")).strip().upper(),
        )
        pattern = usual.get(key)
        if pattern is None or pattern["median_net"] is None:
            continue
        actual = _money(line.get("net_unit_price"))
        if actual is None or actual == pattern["median_net"]:
            continue
        flags.append(
            {
                "line_no": position,
                "display": str(line.get("description", "")).strip()[:120],
                "actual_display": format_eur(actual, currency),
                "usual_display": format_eur(pattern["median_net"], currency),
                "direction": "teurer" if actual > pattern["median_net"] else "günstiger",
                "invoice_count": pattern["invoice_count"],
            }
        )
    return flags


def position_hints_for_contact(
    root: str | Path,
    actor: str,
    contact_id: str,
    *,
    currency: str = "EUR",
    draft: dict[str, Any] | None = None,
    rows: list[dict[str, Any]] | None = None,
    catalog: list[dict[str, Any]] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Build position hints for one contact (see module docstring)."""
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("actor is required")
    if not isinstance(contact_id, str) or not contact_id.strip():
        raise ValueError("contact reference is required")
    normalized_currency = (currency or "EUR").strip().upper() or "EUR"
    try:
        contact = ContactStore(root).get(contact_id, actor)
        if not ContactStore(root).can_manage_contact(contact, actor):
            return {"state": "hidden", "reason": "no_manage_rights"}
    except (PermissionError, ValueError, KeyError, OSError):
        return {"state": "hidden", "reason": "contact_unreadable"}
    try:
        scanned = rows if rows is not None else invoices(Path(root))
        patterns = position_patterns(
            [row for row in scanned if isinstance(row, dict)],
            contact_id,
            normalized_currency,
        )
    except (OSError, ValueError, AttributeError, TypeError):
        return {"state": "hidden", "reason": "invoices_unreadable"}
    if not patterns:
        return {"state": "none"}
    try:
        objects = catalog if catalog is not None else ObjectStore(root).objects()
        existing = catalog_names([item for item in objects if isinstance(item, dict)])
    except (OSError, ValueError, AttributeError, TypeError):
        existing = set()
    day = today if isinstance(today, date) else date.today()
    draft_lines = None
    if isinstance(draft, dict) and str(draft.get("contact_id", "")) == contact_id:
        draft_lines = draft.get("lines")
    all_proposals = article_proposals(patterns, existing, normalized_currency)
    return {
        "state": "ready",
        "contact_id": contact_id,
        "currency": normalized_currency,
        "pattern_count": len(patterns),
        "proposals": all_proposals[:MAX_PANEL_ITEMS],
        "proposal_overflow": max(0, len(all_proposals) - MAX_PANEL_ITEMS),
        "variance": variance_hints(patterns, normalized_currency, day)[:MAX_PANEL_ITEMS],
        "draft_flags": draft_flags(
            draft_lines,
            patterns,
            normalized_currency,
        )[:MAX_PANEL_ITEMS],
        "crm_url": f"/documents/contacts/{quote(contact_id, safe='')}/crm",
    }


def editor_position_hints(
    root: str | Path,
    actor: str,
    contact_id: str,
    draft: dict[str, Any] | None,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Ready-state position hints for the invoice editor (None if nothing)."""
    if not contact_id:
        return None
    currency = str((draft or {}).get("currency", "EUR"))
    try:
        state = position_hints_for_contact(
            root, actor, contact_id, currency=currency, draft=draft, rows=rows
        )
    except (PermissionError, ValueError, OSError, KeyError, AttributeError, TypeError):
        return None
    if state.get("state") != "ready":
        return None
    if not state.get("proposals") and not state.get("variance") and not state.get("draft_flags"):
        return None
    return dict(state)


def parse_article_request(values: dict[str, Any]) -> dict[str, str]:
    """Validate one article-creation form (raises ValueError, German messages)."""
    description = str(values.get("description", "") or "").strip()
    if not description or len(description) > 240:
        raise ValueError("Artikelname muss 1–240 Zeichen haben.")
    unit = str(values.get("unit", "") or "").strip().upper()
    if unit not in INVOICE_UNITS:
        raise ValueError("Unbekannte Einheit.")
    net = _money(values.get("net_price"))
    if net is None or net < 0:
        raise ValueError("Ungültiger Nettopreis.")
    vat = _money(values.get("vat_rate"))
    if vat is None or vat < 0:
        raise ValueError("Ungültiger Mehrwertsteuersatz.")
    return {
        "description": description,
        "unit": unit,
        "net_price": f"{net.quantize(MONEY):.2f}",
        "vat_rate": f"{vat:.2f}",
    }


def find_catalog_duplicate(root: str | Path, description: str) -> str | None:
    """Display id of an invoice-enabled article with the same name (if any)."""
    wanted = normalize_position(description)
    if not wanted:
        return None
    try:
        objects = ObjectStore(root).objects()
    except (OSError, ValueError, AttributeError, TypeError):
        return None
    for item in objects:
        if not isinstance(item, dict):
            continue
        if not _invoice_enabled(item):
            continue
        invoice = item.get("invoice") if isinstance(item.get("invoice"), dict) else {}
        candidates = (item.get("name", ""), invoice.get("description", ""))
        if any(normalize_position(candidate) == wanted for candidate in candidates):
            return str(item.get("display_id", "") or item.get("object_id", ""))
    return None
