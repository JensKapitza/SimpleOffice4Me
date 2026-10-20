"""Read-only FinTS account balance adapter.

This module deliberately exposes no payment methods and does not persist credentials.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .finance_schema import currency as _currency
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
        if hasattr(result, "challenge") or result.__class__.__name__.casefold().startswith("needtan"):
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
        if currency is None:
            currency = getattr(account, "currency", None)
        available = getattr(result, "available_balance", None)
        if available is None:
            available = getattr(result, "available_amount", None)
        if callable(available):
            available = None
        normalized_currency = _currency(currency or "EUR")
        balance = {
            "remote_account_id": remote_account_id,
            "booked_balance": _decimal_amount(booked),
            "currency": normalized_currency,
        }
        balance["available_balance"] = _decimal_amount(available) if available is not None else ""
        balance["available_currency"] = (
            _currency(getattr(available, "currency", None) or normalized_currency)
            if available is not None else ""
        )
        return balance
    finally:
        del client
