"""Tests for the read-only FinTS balance adapter."""
from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.finance_fints import FinTSAuthenticationRequired
from app.finance_fints_balances import _decimal_amount, fetch_balance


class FinTSBalanceTests(TestCase):
    def test_decimal_amount_rejects_non_finite(self):
        with self.assertRaises(ValueError):
            _decimal_amount("NaN")

    def test_decimal_amount_preserves_precision(self):
        self.assertEqual(
            "1234.50",
            _decimal_amount(SimpleNamespace(amount=Decimal("1234.50"))),
        )

    def test_fetch_balance_read_only(self):
        account = object()
        client = SimpleNamespace(
            get_balance=lambda value: SimpleNamespace(
                amount=SimpleNamespace(amount=Decimal("42.25"), currency="EUR")
            )
        )
        with patch("app.finance_fints_balances._client", return_value=client), patch(
            "app.finance_fints_balances._find_remote_account", return_value=account
        ):
            result = fetch_balance(
                {"provider": "fints"}, "iban:DE123", "temporary-pin"
            )
        self.assertEqual(
            {
                "remote_account_id": "iban:DE123",
                "booked_balance": "42.25",
                "currency": "EUR",
            },
            result,
        )
        self.assertNotIn("temporary-pin", repr(result))

    def test_fetch_balance_requires_interactive_authentication(self):
        class NeedTANResponse:
            challenge = "approve in banking app"

        client = SimpleNamespace(get_balance=lambda account: NeedTANResponse())
        with patch("app.finance_fints_balances._client", return_value=client), patch(
            "app.finance_fints_balances._find_remote_account", return_value=object()
        ):
            with self.assertRaises(FinTSAuthenticationRequired):
                fetch_balance(
                    {"provider": "fints"}, "iban:DE123", "temporary-pin"
                )

    def test_fetch_balance_direct_decimal(self):
        client = SimpleNamespace(get_balance=lambda account: Decimal("-17.32"))
        with patch("app.finance_fints_balances._client", return_value=client), patch(
            "app.finance_fints_balances._find_remote_account", return_value=object()
        ):
            result = fetch_balance(
                {"provider": "fints"}, "iban:DE123", "temporary-pin"
            )
        self.assertEqual("-17.32", result["booked_balance"])

    def test_decimal_amount_rejects_overflow(self):
        with self.assertRaises(ValueError):
            _decimal_amount("1e999999")
