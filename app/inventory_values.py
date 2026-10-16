"""Input normalization shared by inventory capture routes."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


def single_line(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:limit]


def fields_text(fields: dict[str, str]) -> str:
    return "\n".join(f"{key}={single_line(value, 1000)}" for key, value in fields.items() if value)


def money(value: Any) -> str:
    raw = str(value or "").strip().replace(" ", "").replace(",", ".")
    if not raw:
        return ""
    try:
        amount = Decimal(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("Preis ist ungültig") from exc
    if not amount.is_finite() or amount < 0 or amount > Decimal("100000000"):
        raise ValueError("Preis liegt außerhalb des zulässigen Bereichs")
    return f"{amount:.2f}"


def date_only(value: Any, label: str = "Datum") -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError as exc:
        raise ValueError(f"{label} ist ungültig") from exc
