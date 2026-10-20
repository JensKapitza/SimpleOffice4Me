"""Read-only FinTS balance SCA capture and continuation contracts."""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.finance_fints import FinTSAuthenticationRequired
from app.finance_fints_balances import fetch_balance


class NeedTANResponse:
    def __init__(self, *, decoupled=False):
        self.challenge = "Freigabe in der Banking-App" if decoupled else "TAN bitte eingeben"
        self.decoupled = decoupled

    def get_data(self):
        return b"retry-response-blob"


class FakeClient:
    def __init__(self, *, balance=None, accounts=None, tan_result=None):
        self.balance = balance
        self.accounts = accounts or []
        self.tan_result = tan_result
        self.sent_tan = None
        self.from_data = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get_sepa_accounts(self):
        return self.accounts

    def get_balance(self, _account):
        return self.balance

    def pause_dialog(self):
        return b"paused-dialog-state"

    def deconstruct(self):
        return b"cached-client-state"

    @contextmanager
    def resume_dialog(self, _dialog_data):
        yield self

    def send_tan(self, _retry, tan):
        self.sent_tan = tan
        return self.tan_result


class FinTSBalanceSCATests(TestCase):
    @staticmethod
    def challenge_state():
        import base64

        encode = lambda value: base64.b64encode(value).decode("ascii")
        return {
            "version": 1,
            "operation": "balance",
            "step": "balance",
            "client_data": encode(b"cached-client-state"),
            "dialog_data": encode(b"paused-dialog-state"),
            "challenge_data": encode(b"retry-response-blob"),
            "remote_account_id": "iban:DE123",
            "account_currency": "EUR",
            "challenge_text": "TAN bitte eingeben",
            "decoupled": False,
        }

    def test_challenge_captures_encrypted_store_payload_without_credentials(self):
        account = SimpleNamespace(currency="EUR")
        client = FakeClient(balance=NeedTANResponse())
        with patch("app.finance_fints_balances._client", return_value=client), patch(
            "app.finance_fints_balances._find_remote_account", return_value=account,
        ):
            with self.assertRaises(FinTSAuthenticationRequired) as raised:
                fetch_balance({"provider": "fints"}, "iban:DE123", "request-pin")

        state = raised.exception.challenge_state
        self.assertEqual("balance", state["step"])
        self.assertEqual("TAN bitte eingeben", state["challenge_text"])
        self.assertFalse(state["decoupled"])
        self.assertEqual(b"cached-client-state", __import__("base64").b64decode(state["client_data"]))
        self.assertNotIn("request-pin", repr(state))

    def test_resume_sends_tan_and_returns_only_balance(self):
        state = self.challenge_state()
        balance = SimpleNamespace(amount=Decimal("42.25"), currency="EUR")
        client = FakeClient(tan_result=balance)
        with patch("app.finance_fints_balances._client", return_value=client) as make_client, patch(
            "app.finance_fints_balances._restore_retry_response", return_value=NeedTANResponse(),
        ):
            result = fetch_balance(
                {"provider": "fints"}, "iban:DE123", "request-pin",
                challenge_state=state, tan="123456",
            )

        self.assertEqual("123456", client.sent_tan)
        self.assertEqual("42.25", result["booked_balance"])
        self.assertEqual("EUR", result["currency"])
        self.assertNotIn("123456", repr(result))
        self.assertEqual(b"cached-client-state", make_client.call_args.kwargs["from_data"])

    def test_resume_refuses_missing_tan_for_non_decoupled_challenge(self):
        state = self.challenge_state()
        client = FakeClient()
        with patch("app.finance_fints_balances._client", return_value=client), patch(
            "app.finance_fints_balances._restore_retry_response", return_value=NeedTANResponse(),
        ):
            with self.assertRaisesRegex(ValueError, "TAN"):
                fetch_balance(
                    {"provider": "fints"}, "iban:DE123", "request-pin",
                    challenge_state=state,
                )

    def test_decoupled_resume_does_not_send_tan_value(self):
        state = self.challenge_state()
        state["decoupled"] = True
        client = FakeClient(tan_result=Decimal("15.00"))
        with patch("app.finance_fints_balances._client", return_value=client), patch(
            "app.finance_fints_balances._restore_retry_response",
            return_value=NeedTANResponse(decoupled=True),
        ):
            result = fetch_balance(
                {"provider": "fints"}, "iban:DE123", "request-pin",
                challenge_state=state, tan="not-sent-to-bank",
            )
        self.assertEqual("", client.sent_tan)
        self.assertEqual("15.00", result["booked_balance"])
