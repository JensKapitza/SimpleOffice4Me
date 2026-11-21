"""FinTS Slice 1 (read-only) acceptance with a mock bank.

Covers what the slice promises: read-only balance retrieval with available
balances, bank-reported read/TAN capabilities with cleanly disabled missing
features, bounded timeout behaviour, error paths, per-user permissions, no
PIN/TAN in logs/DB/responses, and no SEPA/transfer surface.
"""
from __future__ import annotations

import base64
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app
from app.finance_fints import FinTSFeatureUnavailable, discover_accounts_with_capabilities
from app.finance_fints_balances import fetch_balance
from app.finance_store import FinanceStore


MOCK_IBAN = "DE02120300000000202051"
MOCK_REMOTE_ID = "iban:DE02120300000000202051"


class MockBankBalance:
    """Minimal python-fints balance shape: booked amount plus optional funds."""

    def __init__(self, booked, currency="EUR", available=None, available_currency=None):
        self.amount = booked
        self.currency = currency
        if available is not None:
            self.available_balance = SimpleNamespace(
                amount=available, currency=available_currency or currency,
            )


class MockBankClient:
    """Fake FinTS client: accounts, balances, capabilities, no transfers."""

    def __init__(self, *, accounts=None, balance=None, information=None):
        self._accounts = accounts or []
        self._balance = balance
        self._information = information
        self.balance_calls = 0

    def get_sepa_accounts(self):
        return self._accounts

    def get_balance(self, _account):
        self.balance_calls += 1
        return self._balance

    def get_information(self):
        return self._information

    def pause_dialog(self):
        return b"serialized-dialog"

    def deconstruct(self):
        return b"serialized-client"


def mock_account(currency="EUR"):
    return SimpleNamespace(
        iban=MOCK_IBAN,
        bic="BYLADEM1001",
        accountnumber="202051",
        subaccount="",
        bank_identifier=SimpleNamespace(bank_code="12030000"),
        currency=currency,
        account_type="Girokonto",
    )


def mock_capabilities(*, balance_supported=False):
    return {
        "bank": {"supported_operations": {"GET_BALANCE": False, "GET_TRANSACTIONS": True}},
        "accounts": [{
            "iban": MOCK_IBAN,
            "account_number": "202051",
            "supported_operations": {"GET_BALANCE": balance_supported},
        }],
        "auth": {
            "tan_mechanisms": {
                "942": SimpleNamespace(
                    name="pushTAN", VERSION=7, tan_process="2", challenge_structured=False,
                ),
            },
            "current_tan_mechanism": "942",
        },
    }


class FinTSSlice1AdapterTests(unittest.TestCase):
    def test_mock_bank_reports_booked_and_available_balances(self):
        client = MockBankClient(
            balance=MockBankBalance(Decimal("42.25"), available=Decimal("35.50")),
        )
        with patch("app.finance_fints_balances._client", return_value=client), patch(
            "app.finance_fints_balances._find_remote_account", return_value=mock_account()
        ), patch("app.finance_fints_balances._require_read_capability", return_value=None):
            result = fetch_balance({"provider": "fints"}, MOCK_REMOTE_ID, "request-pin")
        self.assertEqual("42.25", result["booked_balance"])
        self.assertEqual("35.50", result["available_balance"])
        self.assertEqual("EUR", result["currency"])
        self.assertEqual(1, client.balance_calls)
        self.assertNotIn("request-pin", repr(result))

    def test_read_only_adapters_expose_no_transfer_or_sepa_api(self):
        import app.finance_fints as fints_adapter
        import app.finance_fints_balances as balances_adapter

        for module in (fints_adapter, balances_adapter):
            forbidden = [
                name for name in dir(module)
                if "transfer" in name.casefold() or name.casefold().startswith("sepa")
            ]
            self.assertEqual([], forbidden, msg=module.__name__)

    def test_mock_bank_capabilities_list_tan_methods_and_disable_balance(self):
        client = MockBankClient(accounts=[mock_account()], information=mock_capabilities())
        with patch("app.finance_fints._client", return_value=client):
            accounts, capabilities = discover_accounts_with_capabilities(
                {"provider": "fints"}, "request-pin",
            )
        self.assertEqual([MOCK_REMOTE_ID], [row["remote_account_id"] for row in accounts])
        self.assertEqual(
            [{"security_function": "942", "name": "pushTAN", "version": 7,
              "tan_process": "2", "challenge_structured": False}],
            capabilities["tan_mechanisms"],
        )
        self.assertEqual("942", capabilities["current_tan_mechanism"])
        self.assertIs(capabilities["read"]["balance"], False)
        self.assertEqual(
            False,
            capabilities["accounts"][MOCK_REMOTE_ID]["balance"],
        )

    def test_unsupported_balance_is_disabled_instead_of_attempted(self):
        client = MockBankClient(accounts=[mock_account()], information=mock_capabilities())
        with patch("app.finance_fints_balances._client", return_value=client), patch(
            "app.finance_fints_balances._find_remote_account", return_value=mock_account()
        ):
            with self.assertRaises(FinTSFeatureUnavailable):
                fetch_balance({"provider": "fints"}, MOCK_REMOTE_ID, "request-pin")
        self.assertEqual(0, client.balance_calls)

    def test_balance_timeout_is_bounded_and_keeps_no_secret(self):
        timeout = TimeoutError("mock bank gateway timed out after 30s")
        with patch(
            "app.finance_fints_balances._client", side_effect=[timeout, timeout, timeout],
        ) as make_client, patch(
            "app.finance_fints_balances._find_remote_account",
        ) as find_account, patch(
            "app.finance_fints_retry.is_retryable_transport_error", return_value=True,
        ), patch("app.finance_fints_retry.time.sleep") as sleep:
            with self.assertRaises(TimeoutError) as raised:
                fetch_balance({"provider": "fints"}, MOCK_REMOTE_ID, "request-pin")
        self.assertEqual(3, make_client.call_count)
        find_account.assert_not_called()
        self.assertEqual(2, sleep.call_count)
        self.assertNotIn("request-pin", str(raised.exception))


class FinTSSlice1HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        keys = ("DATABASE", "DOCUMENT_ROOT", "SECRET_KEY", "TESTING", "TEST_CSRF_PROTECTION")
        self.config = {key: app.config.get(key) for key in keys}
        app.config.update(
            DATABASE=str(self.root / "auth.sqlite3"),
            DOCUMENT_ROOT=str(self.root),
            SECRET_KEY="fints-slice1-test",
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
        )
        with app.app_context():
            from app import db as database
            database.ensure_auth_database()
            db = database.get_db()
            for username in ("slice-owner", "slice-intruder"):
                db.execute(
                    "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                    "VALUES(?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (username, generate_password_hash(username)),
                )
            db.commit()

        self.client = app.test_client()
        self.login("slice-owner")
        with self.client.session_transaction() as session:
            session["_csrf_token"] = "f" * 40
        app.config["TEST_CSRF_PROTECTION"] = True

        self.store = FinanceStore(self.root)
        self.account = self.store.create_account(
            {"name": "Slice Giro", "kind": "bank", "institution": "Testbank", "currency": "EUR"},
            "slice-owner",
        )
        self.connection = self.store.save_bank_connection(
            {
                "provider": "fints", "institution": "Testbank", "bank_code": "12345678",
                "endpoint": "https://fints.example.invalid/", "login_id": "slice-login",
            },
            "slice-owner",
        )
        self.mapping = self.store.map_remote_account(
            self.connection["connection_id"], MOCK_REMOTE_ID,
            self.account["account_id"], MOCK_IBAN, "slice-owner",
        )
        self.url = (
            "/documents/business/bookkeeping/finances/bank-connections/"
            + self.connection["connection_id"]
            + "/balance"
        )

    def tearDown(self):
        app.config.update(self.config)
        self.temp.cleanup()

    def login(self, username):
        self.client.post(
            "/auth/login", data={"username": username, "password": username},
            headers={"X-CSRF-Token": "f" * 40},
        )
        with self.client.session_transaction() as session:
            session["_csrf_token"] = "f" * 40

    def post_balance(self, *, pin="request-pin-9z", tan=None, follow=False):
        data = {"remote_account_id": MOCK_REMOTE_ID, "pin": pin}
        if tan is not None:
            data["tan"] = tan
        return self.client.post(
            self.url, data=data, headers={"X-CSRF-Token": "f" * 40},
            follow_redirects=follow,
        )

    def test_balance_timeout_surfaces_generic_error_without_secret(self):
        with patch(
            "app.finance_web.fetch_balance", side_effect=TimeoutError("mock bank timeout"),
        ) as fetch, self.assertLogs(app.logger, level="WARNING") as captured:
            response = self.post_balance(follow=True)
        fetch.assert_called_once()
        body = response.get_data(as_text=True)
        logs = "\n".join(captured.output)
        self.assertNotIn("request-pin-9z", body)
        self.assertNotIn("request-pin-9z", logs)
        self.assertIn("TimeoutError", logs)

    def test_adversarial_error_text_never_leaks_pin_or_tan(self):
        pin, tan = "request-pin-9z", "tan-7q4x-secret"
        with patch(
            "app.finance_web.fetch_balance",
            side_effect=RuntimeError(f"upstream exploded PIN={pin} TAN={tan}"),
        ), self.assertLogs(app.logger, level="WARNING") as captured:
            response = self.post_balance(pin=pin, tan=tan, follow=True)
        body = response.get_data(as_text=True)
        logs = "\n".join(captured.output)
        flashes = ""
        with self.client.session_transaction() as session:
            flashes = " ".join(str(item) for item in session.get("_flashes", []))
        for surface in (body, logs, flashes):
            self.assertNotIn(pin, surface)
            self.assertNotIn(tan, surface)

    def test_other_user_cannot_read_foreign_balance(self):
        self.login("slice-intruder")
        with patch("app.finance_web.fetch_balance") as fetch:
            response = self.post_balance()
        self.assertEqual(404, response.status_code)
        fetch.assert_not_called()

    def test_challenge_token_survives_only_encrypted_without_secrets(self):
        payload = {
            "version": 1,
            "operation": "balance",
            "step": "balance",
            "client_data": base64.b64encode(b"opaque-client-state").decode("ascii"),
            "dialog_data": base64.b64encode(b"opaque-dialog-state").decode("ascii"),
            "challenge_data": base64.b64encode(b"opaque-challenge-state").decode("ascii"),
            "remote_account_id": MOCK_REMOTE_ID,
            "account_currency": "EUR",
            "challenge_text": "Approve the read-only balance request.",
            "decoupled": True,
        }
        with app.app_context():
            token = self.store.create_fints_challenge(
                self.connection["connection_id"], payload, "slice-owner", now=200,
            )
            with self.store._db() as finance_db:
                row = finance_db.execute(
                    "SELECT payload FROM finance_fints_challenge",
                ).fetchone()
        self.assertTrue(row["payload"].startswith("enc:v1:"))
        self.assertNotIn("request-pin-9z", row["payload"])
        self.assertNotIn("Approve the read-only", row["payload"])
        self.assertNotIn(token, row["payload"])

    def test_finance_bank_routes_offer_no_sepa_or_transfer_action(self):
        finance_rules = [
            str(rule) for rule in app.url_map.iter_rules()
            if ".finance." in (rule.endpoint or "")
        ]
        self.assertGreater(len(finance_rules), 0)
        hits = [
            rule for rule in finance_rules
            if "sepa" in rule.casefold() or "transfer" in rule.casefold()
            or "ueberweis" in rule.casefold()
        ]
        self.assertEqual([], hits)


if __name__ == "__main__":
    unittest.main()
