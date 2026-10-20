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
                "available_balance": "",
                "available_currency": "",
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

    def test_fetch_balance_requires_interactive_authentication_for_account_discovery(self):
        class NeedTANResponse:
            challenge = "approve account discovery"

        client = SimpleNamespace(get_sepa_accounts=lambda: NeedTANResponse())
        with patch("app.finance_fints_balances._client", return_value=client):
            with self.assertRaises(FinTSAuthenticationRequired):
                fetch_balance(
                    {"provider": "fints"}, "iban:DE123", "temporary-pin"
                )

    def test_fetch_balance_direct_decimal(self):
        client = SimpleNamespace(get_balance=lambda account: Decimal("-17.32"))
        with patch("app.finance_fints_balances._client", return_value=client), patch(
            "app.finance_fints_balances._find_remote_account", return_value=SimpleNamespace(currency="CHF")
        ):
            result = fetch_balance(
                {"provider": "fints"}, "iban:DE123", "temporary-pin"
            )
        self.assertEqual("-17.32", result["booked_balance"])
        self.assertEqual("CHF", result["currency"])
        self.assertEqual("", result["available_balance"])
        self.assertEqual("", result["available_currency"])

    def test_available_balance_is_reported_only_when_provider_supplies_it(self):
        account = SimpleNamespace(currency="EUR")
        result = SimpleNamespace(
            amount=Decimal("42.00"),
            available_balance=SimpleNamespace(amount=Decimal("35.50"), currency="CHF"),
            currency="EUR",
        )
        client = SimpleNamespace(get_balance=lambda _account: result)
        with patch("app.finance_fints_balances._client", return_value=client), patch(
            "app.finance_fints_balances._find_remote_account", return_value=account
        ):
            balance = fetch_balance({"provider": "fints"}, "iban:DE123", "temporary-pin")
        self.assertEqual("42.00", balance["booked_balance"])
        self.assertEqual("35.50", balance["available_balance"])
        self.assertEqual("CHF", balance["available_currency"])

    def test_decimal_amount_rejects_overflow(self):
        with self.assertRaises(ValueError):
            _decimal_amount("1e999999")
