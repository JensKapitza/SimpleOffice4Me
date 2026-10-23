"""Read-only FinTS transaction retrieval retains secure SCA continuations."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.finance_fints import FinTSAuthenticationRequired, fetch_transactions


class NeedTANResponse:
    def __init__(self, *, decoupled=False, challenge="TAN bitte eingeben"):
        self.challenge = challenge
        self.decoupled = decoupled

    def get_data(self):
        return b"transaction-retry-state"


class FakeClient:
    def __init__(self, *, accounts=None, transactions=None, tan_result=None):
        self.accounts = accounts or []
        self.transactions = transactions or []
        self.tan_result = tan_result
        self.sent_tan = None
        self.requested_window = None

    def get_sepa_accounts(self):
        return self.accounts

    def get_transactions(self, _account, start, end):
        self.requested_window = (start, end)
        return self.transactions

    def deconstruct(self):
        return b"client-state"

    def pause_dialog(self):
        return b"dialog-state"

    @contextmanager
    def resume_dialog(self, _state):
        yield self

    def send_tan(self, _retry_response, tan):
        self.sent_tan = tan
        return self.tan_result


class FinTSTransactionSCATests(TestCase):
    remote_id = "iban:DE02120300000000202051"
    account = SimpleNamespace(iban="DE02120300000000202051", currency="EUR")
    start = date(2026, 10, 1)
    end = date(2026, 10, 31)
    transaction = {
        "date": date(2026, 10, 22), "amount": Decimal("-9.25"),
        "currency": "EUR", "applicant_name": "Test merchant", "purpose": "Receipt",
    }

    @staticmethod
    def connection():
        return {
            "provider": "fints", "status": "configured", "bank_code": "12345678",
            "login_id": "test-login", "endpoint": "https://fints.example.invalid/",
        }

    def test_initial_transaction_challenge_captures_bounded_nonsecret_state(self):
        client = FakeClient(accounts=[self.account], transactions=NeedTANResponse())
        with patch("app.finance_fints._client", return_value=client):
            with self.assertRaises(FinTSAuthenticationRequired) as raised:
                fetch_transactions(
                    self.connection(), self.remote_id, "request-pin",
                    start_date=self.start, end_date=self.end,
                )

        state = raised.exception.challenge_state
        self.assertEqual("transactions", state["operation"])
        self.assertEqual("transactions", state["step"])
        self.assertEqual(self.start.isoformat(), state["start_date"])
        self.assertEqual(self.end.isoformat(), state["end_date"])
        self.assertEqual("TAN bitte eingeben", state["challenge_text"])
        self.assertNotIn("request-pin", repr(state))

    def test_account_discovery_challenge_resumes_and_fetches_transactions(self):
        first_client = FakeClient(accounts=NeedTANResponse(challenge="TAN für Kontenliste"))
        with patch("app.finance_fints._client", return_value=first_client):
            with self.assertRaises(FinTSAuthenticationRequired) as raised:
                fetch_transactions(
                    self.connection(), self.remote_id, "request-pin",
                    start_date=self.start, end_date=self.end,
                )
        state = raised.exception.challenge_state
        self.assertEqual("accounts", state["step"])

        resumed = FakeClient(tan_result=[self.account], transactions=[self.transaction])
        with patch("app.finance_fints._client", return_value=resumed), patch(
            "app.finance_fints._restore_sca_retry_response", return_value=NeedTANResponse(),
        ):
            rows = fetch_transactions(
                self.connection(), self.remote_id, "request-pin",
                start_date=self.start, end_date=self.end,
                challenge_state=state, tan="654321",
            )

        self.assertEqual("654321", resumed.sent_tan)
        self.assertEqual((self.start, self.end), resumed.requested_window)
        self.assertEqual("-925", str(rows[0]["amount_cents"]))
        self.assertEqual("Test merchant", rows[0]["counterparty_name"])

    def test_transaction_challenge_can_be_repeated_without_importing_partial_rows(self):
        first = FakeClient(accounts=[self.account], transactions=NeedTANResponse())
        with patch("app.finance_fints._client", return_value=first):
            with self.assertRaises(FinTSAuthenticationRequired) as initial:
                fetch_transactions(
                    self.connection(), self.remote_id, "request-pin",
                    start_date=self.start, end_date=self.end,
                )

        second = FakeClient(tan_result=NeedTANResponse(challenge="Weitere TAN"))
        with patch("app.finance_fints._client", return_value=second), patch(
            "app.finance_fints._restore_sca_retry_response", return_value=NeedTANResponse(),
        ):
            with self.assertRaises(FinTSAuthenticationRequired) as repeated:
                fetch_transactions(
                    self.connection(), self.remote_id, "request-pin",
                    start_date=self.start, end_date=self.end,
                    challenge_state=initial.exception.challenge_state, tan="123456",
                )

        self.assertEqual("123456", second.sent_tan)
        self.assertEqual("transactions", repeated.exception.challenge_state["step"])
        self.assertEqual("Weitere TAN", repeated.exception.challenge_text)

    def test_decoupled_transaction_challenge_never_sends_tan(self):
        state = {
            "operation": "transactions", "step": "transactions",
            "remote_account_id": self.remote_id,
            "start_date": self.start.isoformat(), "end_date": self.end.isoformat(),
            "client_data": "Y2xpZW50", "dialog_data": "ZGlhbG9n",
            "challenge_data": "cmV0cnk", "decoupled": True,
        }
        resumed = FakeClient(tan_result=[self.transaction])
        with patch("app.finance_fints._client", return_value=resumed), patch(
            "app.finance_fints._restore_sca_retry_response",
            return_value=NeedTANResponse(decoupled=True),
        ):
            rows = fetch_transactions(
                self.connection(), self.remote_id, "request-pin",
                start_date=self.start, end_date=self.end,
                challenge_state=state, tan="must-not-be-sent",
            )
        self.assertEqual("", resumed.sent_tan)
        self.assertEqual("-925", str(rows[0]["amount_cents"]))


if __name__ == "__main__":
    unittest.main()
