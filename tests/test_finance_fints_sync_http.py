"""HTTP contracts for resumable, read-only FinTS transaction synchronization."""
from __future__ import annotations

import base64
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app, db
from app.finance_fints import FinTSAuthenticationRequired
from app.finance_store import FinanceStore


class FinTSTransactionSyncHTTPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        config = ("DATABASE", "DOCUMENT_ROOT", "SECRET_KEY", "TESTING", "TEST_CSRF_PROTECTION")
        self.previous = {key: app.config.get(key) for key in config}
        self.addCleanup(lambda: app.config.update(self.previous))
        app.config.update(
            DATABASE=str(self.root / "auth.sqlite3"),
            DOCUMENT_ROOT=str(self.root),
            SECRET_KEY="fints-transaction-sync-test",
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
        )
        with app.app_context():
            db.ensure_auth_database()
            connection = db.get_db()
            connection.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES(?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("sync-user", generate_password_hash("synthetic-sync-password")),
            )
            connection.commit()
        self.store = FinanceStore(self.root)
        account = self.store.create_account({"name": "Read-only", "kind": "bank"}, "sync-user")
        self.connection = self.store.save_bank_connection({
            "provider": "fints", "institution": "Testbank", "bank_code": "12345678",
            "endpoint": "https://fints.example.invalid/", "login_id": "test-login",
        }, "sync-user")
        self.mapping = self.store.map_remote_account(
            self.connection["connection_id"], "iban:DE02120300000000202051",
            account["account_id"], "DE02120300000000202051", "sync-user",
        )
        self.client = app.test_client()
        self.client.post("/auth/login", data={"username": "sync-user", "password": "synthetic-sync-password"})
        with self.client.session_transaction() as session:
            session["_csrf_token"] = "f" * 40
        self.url = f"/documents/business/bookkeeping/finances/bank-connections/{self.connection['connection_id']}/sync"
        self.challenge_state = {
            "version": 1,
            "operation": "transactions",
            "step": "transactions",
            "client_data": base64.b64encode(b"client-state").decode("ascii"),
            "dialog_data": base64.b64encode(b"dialog-state").decode("ascii"),
            "challenge_data": base64.b64encode(b"retry-state").decode("ascii"),
            "remote_account_id": self.mapping["remote_account_id"],
            "account_currency": "EUR",
            "challenge_text": "Freigabe für Umsatzabruf",
            "decoupled": False,
            "start_date": "2026-10-01",
            "end_date": "2026-10-31",
            "local_account_id": account["account_id"],
            "completed_mappings": [],
        }

    def test_sync_challenge_is_encrypted_then_resumed_once_with_tan(self):
        challenge = FinTSAuthenticationRequired(
            "TAN required", challenge_state=self.challenge_state,
            challenge_text=self.challenge_state["challenge_text"],
        )
        with patch("app.finance_web.FinTSSyncService.sync_connection", side_effect=challenge):
            initial = self.client.post(
                self.url, data={"pin": "temporary-pin"}, headers={"X-CSRF-Token": "f" * 40},
            )
        self.assertEqual(
            200, initial.status_code,
            f"location={initial.headers.get('Location')}; body={initial.get_data(as_text=True)[:1000]}",
        )
        self.assertEqual("no-store", initial.headers["Cache-Control"])
        self.assertIn("Freigabe für Umsatzabruf", initial.get_data(as_text=True))
        token_match = re.search(
            r'name="challenge_token" value="([A-Za-z0-9_-]{43})"',
            initial.get_data(as_text=True),
        )
        self.assertIsNotNone(token_match)
        token = token_match.group(1)
        with app.app_context():
            with self.store._db() as finance_db:
                stored = finance_db.execute(
                    "SELECT operation,payload FROM finance_fints_challenge WHERE operation='transactions'",
                ).fetchone()
            self.assertEqual("transactions", stored["operation"])
            self.assertTrue(stored["payload"].startswith("enc:v1:"))
            self.assertNotIn("temporary-pin", stored["payload"])

        result = {
            "connection_id": self.connection["connection_id"], "accounts": [],
            "fetched": 0, "created": 0, "existing": 0, "possible_duplicates": 0,
            "coverage": "not_verified", "previously_synced_accounts": [],
        }
        with patch("app.finance_web.FinTSSyncService.sync_connection", return_value=result) as resume:
            response = self.client.post(
                self.url + "/challenge",
                data={"challenge_token": token, "pin": "temporary-pin", "tan": "123456"},
                headers={"X-CSRF-Token": "f" * 40},
            )
        self.assertEqual(
            200, response.status_code,
            f"location={response.headers.get('Location')}; body={response.get_data(as_text=True)[:1000]}",
        )
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertNotIn("123456", response.get_data(as_text=True))
        resume.assert_called_once_with(
            self.connection["connection_id"], "temporary-pin", "sync-user",
            challenge_state=self.challenge_state, tan="123456",
        )
        with app.app_context():
            with self.assertRaisesRegex(ValueError, "ungültig oder abgelaufen"):
                self.store.consume_fints_challenge(
                    token, self.connection["connection_id"], "sync-user",
                )

    def test_transaction_resume_route_rejects_csrf_and_single_use(self):
        with app.app_context():
            token = self.store.create_fints_challenge(
                self.connection["connection_id"], self.challenge_state, "sync-user",
            )
        with patch("app.finance_web.FinTSSyncService.sync_connection") as sync:
            response = self.client.post(
                self.url + "/challenge",
                data={"challenge_token": token, "pin": "temporary-pin", "tan": "123456"},
            )
        self.assertEqual(403, response.status_code)
        sync.assert_not_called()

        result = {
            "connection_id": self.connection["connection_id"], "accounts": [],
            "fetched": 0, "created": 0, "existing": 0, "possible_duplicates": 0,
            "coverage": "not_verified", "previously_synced_accounts": [],
        }
        with patch("app.finance_web.FinTSSyncService.sync_connection", return_value=result) as sync:
            response = self.client.post(
                self.url + "/challenge",
                data={"challenge_token": token, "pin": "temporary-pin", "tan": "123456"},
                headers={"X-CSRF-Token": "f" * 40},
            )
        self.assertEqual(200, response.status_code)
        sync.assert_called_once()
        with patch("app.finance_web.FinTSSyncService.sync_connection") as sync:
            response = self.client.post(
                self.url + "/challenge",
                data={"challenge_token": token, "pin": "temporary-pin", "tan": "123456"},
                headers={"X-CSRF-Token": "f" * 40},
            )
        self.assertEqual(302, response.status_code)
        sync.assert_not_called()


if __name__ == "__main__":
    unittest.main()
