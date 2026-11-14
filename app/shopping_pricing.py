"""Locale-tolerant price parsing for estimated shopping-list totals."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any


_MAX_VALUE = Decimal("1000000000")


def parse_decimal(value: Any) -> Decimal | None:
    raw = str(value or "").strip().replace("€", "").replace("EUR", "").replace("eur", "")
    raw = re.sub(r"\s+", "", raw)
    if not raw:
        return None
    if not re.fullmatch(r"\d[\d.,]*", raw):
        raise ValueError("Ungültiger Preis oder Mengenwert")
    if "," in raw and "." in raw:
        decimal_mark = "," if raw.rfind(",") > raw.rfind(".") else "."
        grouping_mark = "." if decimal_mark == "," else ","
        raw = raw.replace(grouping_mark, "").replace(decimal_mark, ".")
    elif "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        result = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("Ungültiger Preis oder Mengenwert") from exc
    if not result.is_finite() or result < 0 or result > _MAX_VALUE:
        raise ValueError("Preis und Menge müssen gültige positive Zahlen sein")
    return result


def estimated_total(items: list[dict[str, Any]]) -> dict[str, Any]:
    total = Decimal("0")
    priced = 0
    missing = 0
    for item in items:
        if item.get("status") == "bought":
            continue
        try:
            price = parse_decimal(item.get("price", ""))
            quantity = parse_decimal(item.get("quantity", ""))
        except ValueError:
            missing += 1
            continue
        if price is None:
            missing += 1
            continue
        total += price * (quantity if quantity is not None else Decimal("1"))
        priced += 1
    return {
        "amount": total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "priced_items": priced,
        "missing_prices": missing,
    }


def display_eur(value: Decimal) -> str:
    formatted = f"{value:,.2f}"
    return formatted.replace(",", "\u0000").replace(".", ",").replace("\u0000", ".") + " €"


def format_quantity(value: Decimal) -> str:
    normalized = format(value.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized.replace(".", ",") or "0"
