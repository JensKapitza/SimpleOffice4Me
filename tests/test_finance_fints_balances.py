"""Tests for the read-only FinTS balance adapter."""
from decimal import Decimal
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from app.finance_fints import FinTSAuthenticationRequired
from app.finance_fints_balances import _decimal_amount, fetch_balance


class FakeFinTSClient:
    def __init__(self, *, accounts=None, balance=None, resumed=None):
        self.accounts = accounts or []
        self.balance = balance
        self.resumed = resumed
        self.sent_tan = None

    def __enter__(self):
        return self

    def __exit__(self, _kind, _value, _traceback):
        return False

    @contextmanager
    def resume_dialog(self, _dialog):
        yield self

    def get_sepa_accounts(self):
        return self.accounts

    def get_balance(self, _account):
        return self.balance

    def pause_dialog(self):
        return b"serialized-dialog"

    def deconstruct(self):
        return b"serialized-client"

    def send_tan(self, _response, tan):
        self.sent_tan = tan
        return self.resumed


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

    def test_fresh_balance_read_retries_transient_transport_failure(self):
        account = SimpleNamespace(currency="EUR")
        failed_client = FakeFinTSClient()
        failed_client.get_balance = Mock(side_effect=ConnectionResetError("temporary disconnect"))
        success_client = FakeFinTSClient(balance=SimpleNamespace(
            amount=Decimal("42.25"), currency="EUR",
        ))

        with patch("app.finance_fints_balances._client", side_effect=[failed_client, success_client]) as make_client, patch(
            "app.finance_fints_balances._find_remote_account", return_value=account,
        ), patch(
            "app.finance_fints_retry.is_retryable_transport_error", return_value=True,
        ) as is_retryable, patch("app.finance_fints_retry.time.sleep") as sleep:
            result = fetch_balance(
                {"provider": "fints"}, "iban:DE123", "temporary-pin",
            )

        self.assertEqual("42.25", result["booked_balance"])
        self.assertEqual(2, make_client.call_count)
        self.assertEqual(1, is_retryable.call_count)
        failed_client.get_balance.assert_called_once()
        sleep.assert_called_once()

    def test_fetch_balance_requires_interactive_authentication(self):
        class NeedTANResponse:
            challenge = "approve in banking app"
            decoupled = False

            def get_data(self):
                return b"retry-state"

        client = FakeFinTSClient(balance=NeedTANResponse())
        with patch("app.finance_fints_balances._client", return_value=client), patch(
            "app.finance_fints_balances._find_remote_account", return_value=object()
        ):
            with self.assertRaises(FinTSAuthenticationRequired) as raised:
                fetch_balance(
                    {"provider": "fints"}, "iban:DE123", "temporary-pin"
                )
        state = raised.exception.challenge_state
        self.assertEqual("balance", state["step"])
        self.assertEqual("approve in banking app", state["challenge_text"])
        self.assertNotIn("temporary-pin", repr(state))

    def test_fetch_balance_requires_interactive_authentication_for_account_discovery(self):
        class NeedTANResponse:
            challenge = "approve account discovery"
            decoupled = True

            def get_data(self):
                return b"retry-state"

        client = FakeFinTSClient(accounts=NeedTANResponse())
        with patch("app.finance_fints_balances._client", return_value=client):
            with self.assertRaises(FinTSAuthenticationRequired):
                fetch_balance(
                    {"provider": "fints"}, "iban:DE123", "temporary-pin"
                )

    def test_fetch_balance_resumes_balance_challenge_with_request_local_tan(self):
        class NeedTANResponse:
            challenge = "Enter the TAN"
            decoupled = False

            def get_data(self):
                return b"retry-state"

        client = FakeFinTSClient(resumed=Decimal("42.25"))
        state = {
            "operation": "balance",
            "step": "balance",
            "client_data": "Y2xpZW50",
            "dialog_data": "ZGlhbG9n",
            "challenge_data": "cmV0cnk=",
            "remote_account_id": "iban:DE123",
            "account_currency": "EUR",
            "challenge_text": "Enter the TAN",
            "decoupled": False,
        }
        with patch("app.finance_fints_balances._client", return_value=client), patch(
            "app.finance_fints_balances._restore_retry_response", return_value=NeedTANResponse()
        ):
            result = fetch_balance(
                {"provider": "fints"}, "iban:DE123", "temporary-pin",
                challenge_state=state, tan="request-local-tan",
            )
        self.assertEqual("42.25", result["booked_balance"])
        self.assertEqual("EUR", result["currency"])
        self.assertEqual("request-local-tan", client.sent_tan)

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
