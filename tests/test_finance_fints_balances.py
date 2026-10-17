"""Tests for the read-only FinTS balance adapter."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.finance_fints import FinTSAuthenticationRequired
from app.finance_fints_balances import _decimal_amount, fetch_balance


def test_decimal_amount_rejects_non_finite():
    with pytest.raises(ValueError):
        _decimal_amount("NaN")


def test_decimal_amount_preserves_precision():
    assert _decimal_amount(SimpleNamespace(amount=Decimal("1234.50"))) == "1234.50"


def test_fetch_balance_read_only():
    account = object()
    client = SimpleNamespace(get_balance=lambda value: SimpleNamespace(
        amount=SimpleNamespace(amount=Decimal("42.25"), currency="EUR")
    ))
    with patch("app.finance_fints_balances._client", return_value=client), patch(
        "app.finance_fints_balances._find_remote_account", return_value=account
    ):
        result = fetch_balance({"provider": "fints"}, "iban:DE123", "temporary-pin")
    assert result == {"remote_account_id": "iban:DE123", "booked_balance": "42.25", "currency": "EUR"}
    assert "temporary-pin" not in repr(result)


def test_fetch_balance_requires_interactive_authentication():
    class NeedTANResponse:
        challenge = "approve in banking app"

    client = SimpleNamespace(get_balance=lambda account: NeedTANResponse())
    with patch("app.finance_fints_balances._client", return_value=client), patch(
        "app.finance_fints_balances._find_remote_account", return_value=object()
    ):
        with pytest.raises(FinTSAuthenticationRequired):
            fetch_balance({"provider": "fints"}, "iban:DE123", "temporary-pin")
