"""Read-only FinTS account balance adapter.

This module deliberately exposes no payment methods and does not persist credentials.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .finance_fints import FinTSAuthenticationRequired, _client, _find_remote_account


def _decimal_amount(value: Any) -> str:
    """Normalize python-fints balance amounts without floating-point rounding."""
    if hasattr(value, "amount"):
        value = value.amount
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("FinTS lieferte einen ungültigen Kontostand") from exc
    if not amount.is_finite():
        raise ValueError("FinTS lieferte einen ungültigen Kontostand")
    try:
        return str(amount.quantize(Decimal("0.01")))
    except InvalidOperation as exc:
        raise ValueError("FinTS lieferte einen ungültigen Kontostand") from exc


def fetch_balance(connection: dict[str, Any], remote_account_id: str, pin: str) -> dict[str, str]:
    """Fetch the booked account balance; fail closed if interactive SCA is needed."""
    client = _client(connection, pin)
    try:
        account = _find_remote_account(client, remote_account_id)
        result = client.get_balance(account)
        if result.__class__.__name__.casefold().startswith("needtan"):
            raise FinTSAuthenticationRequired("Die Bank verlangt eine TAN/App-Freigabe für die Saldoabfrage.")
        if result is None:
            raise ValueError("FinTS lieferte keinen Kontostand")
        booked = getattr(result, "amount", None)
        if booked is None and isinstance(result, (Decimal, int, str)):
            booked = result
        if booked is None:
            raise ValueError("FinTS lieferte keinen gebuchten Kontostand")
        currency = getattr(result, "currency", None)
        if currency is None:
            currency = getattr(booked, "currency", None)
        return {
            "remote_account_id": remote_account_id,
            "booked_balance": _decimal_amount(booked),
            "currency": str(currency or "EUR").upper(),
        }
    finally:
        del client
