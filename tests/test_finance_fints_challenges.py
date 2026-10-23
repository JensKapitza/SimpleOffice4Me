"""Bounded, encrypted FinTS SCA continuation storage contracts."""
from __future__ import annotations

import base64
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import app
from app.document_store import CONTROL_DIR
from app.finance_store import (
    FINTS_CHALLENGE_TTL_SECONDS,
    FinanceStore,
)


class FinTSChallengeStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.previous_secret = app.config.get("SECRET_KEY")
        app.config["SECRET_KEY"] = "finance-fints-challenge-test-key"
        self.store = FinanceStore(self.root)
        with app.app_context():
            account = self.store.create_account({"name": "Read-only test"}, "challenge-user")
            self.connection = self.store.save_bank_connection(
                {
                    "provider": "fints",
                    "institution": "Testbank",
                    "bank_code": "12345678",
                    "endpoint": "https://fints.example.invalid/",
                    "login_id": "test-login",
                },
                "challenge-user",
            )
            self.store.map_remote_account(
                self.connection["connection_id"],
                "iban:DE02120300000000202051",
                account["account_id"],
                "DE02120300000000202051",
                "challenge-user",
            )

    def tearDown(self):
        app.config["SECRET_KEY"] = self.previous_secret
        self.temp.cleanup()

    @staticmethod
    def state(**overrides):
        payload = {
            "version": 1,
            "operation": "balance",
            "step": "balance",
            "client_data": base64.b64encode(b"opaque-client-state").decode("ascii"),
            "dialog_data": base64.b64encode(b"opaque-dialog-state").decode("ascii"),
            "challenge_data": base64.b64encode(b"opaque-bank-challenge").decode("ascii"),
            "remote_account_id": "iban:DE02120300000000202051",
            "account_currency": "EUR",
            "challenge_text": "Approve the read-only balance request",
            "decoupled": True,
        }
        payload.update(overrides)
        return payload

    def test_state_is_encrypted_owner_scoped_and_consumed_once(self):
        payload = self.state()
        with app.app_context():
            token = self.store.create_fints_challenge(
                self.connection["connection_id"], payload, "challenge-user",
            )
            with self.store._db() as db:
                row = db.execute(
                    "SELECT token_hash,payload FROM finance_fints_challenge",
                ).fetchone()
            self.assertTrue(row["payload"].startswith("enc:v1:"))
            self.assertNotIn(payload["challenge_text"], row["payload"])
            self.assertNotEqual(token, row["token_hash"])

            with self.assertRaisesRegex(ValueError, "ungültig oder abgelaufen"):
                self.store.consume_fints_challenge(
                    token, self.connection["connection_id"], "another-user",
                )
            restored = self.store.consume_fints_challenge(
                token, self.connection["connection_id"], "challenge-user",
            )
            self.assertEqual(payload, restored)
            with self.assertRaisesRegex(ValueError, "ungültig oder abgelaufen"):
                self.store.consume_fints_challenge(
                    token, self.connection["connection_id"], "challenge-user",
                )

    def test_expired_state_is_deleted_and_never_resumed(self):
        with app.app_context():
            token = self.store.create_fints_challenge(
                self.connection["connection_id"], self.state(), "challenge-user", now=100,
            )
            with self.assertRaisesRegex(ValueError, "ungültig oder abgelaufen"):
                self.store.consume_fints_challenge(
                    token,
                    self.connection["connection_id"],
                    "challenge-user",
                    now=100 + FINTS_CHALLENGE_TTL_SECONDS,
                )
            with self.store._db() as db:
                self.assertEqual(
                    0,
                    db.execute("SELECT COUNT(*) AS count FROM finance_fints_challenge").fetchone()["count"],
                )

    def test_expired_rows_are_committed_away_before_capacity_checks(self):
        with app.app_context():
            for _ in range(3):
                self.store.create_fints_challenge(
                    self.connection["connection_id"], self.state(), "challenge-user", now=100,
                )
            self.store.create_fints_challenge(
                self.connection["connection_id"], self.state(), "challenge-user",
                now=100 + FINTS_CHALLENGE_TTL_SECONDS,
            )
            with self.store._db() as db:
                active = db.execute(
                    "SELECT COUNT(*) AS count FROM finance_fints_challenge",
                ).fetchone()["count"]
        self.assertEqual(1, active)

    def test_state_rejects_credentials_and_unbounded_payloads(self):
        with app.app_context():
            for payload in (
                self.state(pin="bank-pin"),
                self.state(tan="one-time-tan"),
                self.state(metadata={"bank": {"pin": "bank-pin"}}),
                self.state(challenge_text="x" * 2001),
            ):
                with self.subTest(payload_keys=tuple(payload)):
                    with self.assertRaises(ValueError):
                        self.store.create_fints_challenge(
                            self.connection["connection_id"], payload, "challenge-user",
                        )

    def test_transaction_challenge_binds_dates_and_local_mapping(self):
        mapping = self.store.bank_account_mappings(
            self.connection["connection_id"], "challenge-user",
        )[0]
        payload = self.state(
            operation="transactions",
            step="transactions",
            start_date="2026-10-01",
            end_date="2026-10-31",
            local_account_id=mapping["account_id"],
            completed_mappings=[],
        )
        with app.app_context():
            token = self.store.create_fints_challenge(
                self.connection["connection_id"], payload, "challenge-user",
            )
            with self.store._db() as db:
                row = db.execute(
                    "SELECT operation,payload FROM finance_fints_challenge",
                ).fetchone()
            self.assertEqual("transactions", row["operation"])
            self.assertTrue(row["payload"].startswith("enc:v1:"))
            restored = self.store.consume_fints_challenge(
                token, self.connection["connection_id"], "challenge-user",
            )
        self.assertEqual(payload, restored)

    def test_transaction_challenge_rejects_invalid_windows_and_mapping_changes(self):
        mapping = self.store.bank_account_mappings(
            self.connection["connection_id"], "challenge-user",
        )[0]
        payload = self.state(
            operation="transactions",
            step="transactions",
            start_date="2026-10-31",
            end_date="2026-10-01",
            local_account_id=mapping["account_id"],
            completed_mappings=[],
        )
        with app.app_context(), self.assertRaisesRegex(ValueError, "Zeitraum"):
            self.store.create_fints_challenge(
                self.connection["connection_id"], payload, "challenge-user",
            )

        payload["start_date"] = "2026-10-01"
        with app.app_context():
            token = self.store.create_fints_challenge(
                self.connection["connection_id"], payload, "challenge-user",
            )
            other = self.store.create_account({"name": "Other read-only test"}, "challenge-user")
            self.store.map_remote_account(
                self.connection["connection_id"], "iban:DE02120300000000202051",
                other["account_id"], "DE02120300000000202051", "challenge-user",
            )
            with self.assertRaisesRegex(ValueError, "Kontozuordnung"):
                self.store.consume_fints_challenge(
                    token, self.connection["connection_id"], "challenge-user",
                )

    def test_existing_challenge_rows_migrate_without_losing_payloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control = root / CONTROL_DIR
            control.mkdir(parents=True)
            database_path = control / "finance.sqlite3"
            with sqlite3.connect(database_path) as database:
                database.execute(
                    "CREATE TABLE finance_fints_challenge("
                    "token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, connection_id TEXT NOT NULL,"
                    "payload TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL)"
                )
                database.execute(
                    "INSERT INTO finance_fints_challenge VALUES(?,?,?,?,?,?)",
                    ("a" * 64, "legacy-user", "legacy-connection", "enc:v1:legacy", 100, 200),
                )

            FinanceStore(root)
            with sqlite3.connect(database_path) as database:
                row = database.execute(
                    "SELECT operation,payload FROM finance_fints_challenge WHERE token_hash=?",
                    ("a" * 64,),
                ).fetchone()
            self.assertEqual(("balance", "enc:v1:legacy"), row)


if __name__ == "__main__":
    unittest.main()
