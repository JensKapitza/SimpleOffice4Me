"""HTTP contracts for explicit, read-only FinTS balance requests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app, db as database
from app.finance_fints import FinTSAuthenticationRequired
from app.finance_store import FinanceStore


class FinTSBalanceHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        keys = ("DATABASE", "DOCUMENT_ROOT", "SECRET_KEY", "TESTING", "TEST_CSRF_PROTECTION")
        self.config = {key: app.config.get(key) for key in keys}
        app.config.update(
            DATABASE=str(self.root / "auth.sqlite3"),
            DOCUMENT_ROOT=str(self.root),
            SECRET_KEY="fints-balance-http-test",
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES(?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("balance-user", generate_password_hash("balance-user")),
            )
            db.commit()

        self.client = app.test_client()
        self.client.post(
            "/auth/login", data={"username": "balance-user", "password": "balance-user"},
        )
        with self.client.session_transaction() as session:
            session["_csrf_token"] = "f" * 40
        app.config["TEST_CSRF_PROTECTION"] = True

        self.store = FinanceStore(self.root)
        self.account = self.store.create_account(
            {"name": "Test Giro", "kind": "bank", "institution": "Testbank", "currency": "EUR"},
            "balance-user",
        )
        self.connection = self.store.save_bank_connection(
            {
                "provider": "fints", "institution": "Testbank", "bank_code": "12345678",
                "endpoint": "https://fints.example.invalid/", "login_id": "test-login",
            },
            "balance-user",
        )
        self.mapping = self.store.map_remote_account(
            self.connection["connection_id"], "iban:DE02120300000000202051",
            self.account["account_id"], "DE02120300000000202051", "balance-user",
        )
        self.url = (
            "/documents/business/bookkeeping/finances/bank-connections/"
            + self.connection["connection_id"]
            + "/balance"
        )

    def tearDown(self):
        app.config.update(self.config)
        self.temp.cleanup()

    def post_balance(self, *, remote_account_id=None, pin="temporary-pin", csrf=True):
        headers = {"X-CSRF-Token": "f" * 40} if csrf else {}
        return self.client.post(
            self.url,
            data={"remote_account_id": remote_account_id or self.mapping["remote_account_id"], "pin": pin},
            headers=headers,
        )

    def test_mapped_account_has_a_pin_only_read_balance_action(self):
        response = self.client.get("/documents/business/bookkeeping/finances")
        body = response.get_data(as_text=True)

        self.assertEqual(200, response.status_code)
        self.assertIn("Kontostand lesen", body)
        self.assertIn(f'value="{self.mapping["remote_account_id"]}"', body)
        self.assertNotIn("temporary-pin", body)

    def test_mapped_balance_is_transient_and_not_cacheable(self):
        result = {
            "remote_account_id": self.mapping["remote_account_id"],
            "booked_balance": "42.25",
            "available_balance": "35.50",
            "currency": "EUR",
        }
        with patch("app.finance_web.fetch_balance", return_value=result) as fetch:
            response = self.post_balance()

        self.assertEqual(200, response.status_code)
        self.assertIn("42.25 EUR", response.get_data(as_text=True))
        self.assertIn("35.50 EUR", response.get_data(as_text=True))
        self.assertNotIn("temporary-pin", response.get_data(as_text=True))
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertEqual("no-referrer", response.headers["Referrer-Policy"])
        fetch.assert_called_once_with(self.connection, self.mapping["remote_account_id"], "temporary-pin")
        self.assertEqual([], self.store.bank_transactions(self.account["account_id"], "balance-user"))
        self.assertNotIn("pin", self.store.bank_connection(self.connection["connection_id"], "balance-user"))

    def test_unmapped_remote_account_and_missing_csrf_are_rejected_before_fetch(self):
        with patch("app.finance_web.fetch_balance") as fetch:
            self.assertEqual(403, self.post_balance(csrf=False).status_code)
            self.assertEqual(404, self.post_balance(remote_account_id="iban:other").status_code)
        fetch.assert_not_called()

    def test_disabled_connection_never_attempts_a_balance_read(self):
        self.store.update_bank_connection_status(
            self.connection["connection_id"], "balance-user", "disabled",
        )
        with patch("app.finance_web.fetch_balance") as fetch:
            response = self.post_balance()
        self.assertEqual(404, response.status_code)
        fetch.assert_not_called()

    def test_tan_challenge_is_reported_without_retrying_or_persisting_credentials(self):
        with patch(
            "app.finance_web.fetch_balance",
            side_effect=FinTSAuthenticationRequired("TAN required"),
        ) as fetch:
            response = self.post_balance()

        self.assertEqual(302, response.status_code)
        self.assertEqual(1, fetch.call_count)
        self.assertNotIn("temporary-pin", repr(self.store.bank_connection(
            self.connection["connection_id"], "balance-user",
        )))
        self.assertEqual([], self.store.bank_transactions(self.account["account_id"], "balance-user"))


if __name__ == "__main__":
    unittest.main()
