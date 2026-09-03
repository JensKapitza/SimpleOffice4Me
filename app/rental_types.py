"""Shared types and deterministic helpers for rental billing."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

MONEY = Decimal("0.01")
CALCULATION_VERSION = "rental-allocation-v1"
STATUSES = {"draft", "review", "approved", "sent", "void", "corrected"}
EDITABLE_STATUSES = {"draft", "review"}
ALLOCATION_METHODS = {
    "direct", "equal", "area", "percent", "shares", "consumption",
    "persons", "person_days", "manual",
}
METRIC_TYPES = {"area", "percent", "shares", "consumption", "persons"}
LEDGER_KINDS = {"advance", "payment", "credit", "opening_balance", "charge", "adjustment"}
SOURCE_KINDS = {"document", "manual", "import", "system"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def money(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0").replace(",", ".")).quantize(MONEY, ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError("Ungültiger Geldbetrag") from None


def number(value: Any) -> Decimal:
    try:
        result = Decimal(str(value or "0").replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValueError("Ungültiger Zahlenwert") from None
    if not result.is_finite():
        raise ValueError("Zahlenwert muss endlich sein")
    return result


def parse_date(value: Any, *, optional: bool = False) -> date | None:
    text = str(value or "").strip()
    if optional and not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError("Ungültiges Datum") from None


def iso(value: date | None) -> str:
    return value.isoformat() if value else ""


def days(start: date, end: date) -> int:
    return max(0, (end - start).days + 1) if end >= start else 0


def intersection(a_start: date, a_end: date, b_start: date, b_end: date) -> tuple[date, date] | None:
    start, end = max(a_start, b_start), min(a_end, b_end)
    return (start, end) if start <= end else None


def safe_name(value: str, fallback: str = "datei") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-.")
    return (cleaned or fallback)[:120]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def allocate_money(total: Decimal, raw_weights: dict[str, Decimal]) -> dict[str, Decimal]:
    """Allocate exact cents deterministically and preserve the requested total."""
    weights = {key: max(Decimal("0"), Decimal(value)) for key, value in raw_weights.items()}
    positive = {key: value for key, value in weights.items() if value > 0}
    if not positive:
        raise ValueError("Verteilungsschlüssel enthält keine positiven Werte")
    total = total.quantize(MONEY, ROUND_HALF_UP)
    weight_sum = sum(positive.values(), Decimal("0"))
    exact = {key: total * value / weight_sum for key, value in positive.items()}
    rounded = {key: value.quantize(MONEY, ROUND_HALF_UP) for key, value in exact.items()}
    delta = total - sum(rounded.values(), Decimal("0"))
    if delta:
        cent = MONEY if delta > 0 else -MONEY
        order = sorted(positive, key=lambda key: (abs(exact[key] - rounded[key]), key), reverse=True)
        index = 0
        while delta:
            key = order[index % len(order)]
            rounded[key] += cent
            delta -= cent
            index += 1
    return {key: rounded.get(key, Decimal("0.00")) for key in raw_weights}
