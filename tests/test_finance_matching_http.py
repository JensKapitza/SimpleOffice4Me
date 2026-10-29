"""HTTP authorization and integrity contracts for finance match proposals."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app, db
from app.finance_store import FinanceStore


class FinanceMatchingHTTPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        config_keys = (
            "DATABASE", "DOCUMENT_ROOT", "SECRET_KEY", "TESTING", "TEST_CSRF_PROTECTION",
        )
        previous = {key: app.config.get(key) for key in config_keys}
        self.addCleanup(lambda: app.config.update(previous))
        app.config.update(
            DATABASE=str(self.root / "auth.sqlite3"),
            DOCUMENT_ROOT=str(self.root),
            SECRET_KEY="finance-match-http-test",
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
        )
        with app.app_context():
            db.ensure_auth_database()
            auth_db = db.get_db()
            auth_db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES(?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("match-user", generate_password_hash("match-password")),
            )
            auth_db.commit()
        self.store = FinanceStore(self.root)
        self.account = self.store.create_account({"name": "Giro"}, "match-user")
        self.transaction, _ = self.store.import_bank_transaction({
            "account_id": self.account["account_id"],
            "booking_date": "2026-10-01",
            "amount_cents": -12345,
            "counterparty_name": "Kita Muster",
            "purpose": "Rechnung KITA-4711",
        }, "match-user")
        self.client = app.test_client()
        self.client.post(
            "/auth/login", data={"username": "match-user", "password": "match-password"},
        )
        with self.client.session_transaction() as session:
            session["_csrf_token"] = "m" * 40
        app.config["TEST_CSRF_PROTECTION"] = True
        self.url = (
            "/documents/business/bookkeeping/finances/transactions/"
            f"{self.transaction['transaction_id']}/matches"
        )

    def test_match_page_is_private_and_does_not_render_a_client_score_field(self):
        proposal = {
            "source_type": "contract", "source_id": "contract-1", "score": 85,
            "reasons": ["Referenz steht im Verwendungszweck"],
        }
        with patch("app.finance_web.proposals_for_transaction", return_value=[proposal]):
            response = self.client.get(self.url)
        self.assertEqual(200, response.status_code)
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertIn("Zuordnung bestätigen", response.get_data(as_text=True))
        self.assertNotIn('name="score"', response.get_data(as_text=True))

    def test_confirmation_rejects_forged_candidate_and_keeps_bank_transaction_unchanged(self):
        proposal = {"source_type": "contract", "source_id": "current-contract", "score": 85}
        original = self.store.bank_transaction(self.transaction["transaction_id"], "match-user")
        with patch("app.finance_web.proposals_for_transaction", return_value=[proposal]):
            response = self.client.post(
                self.url,
                data={
                    "source_type": "contract", "source_id": "forged-contract",
                    "score": "100", "allocated_amount": "123.45",
                },
                headers={"X-CSRF-Token": "m" * 40},
            )
        self.assertEqual(302, response.status_code)
        self.assertEqual([], self.store.transaction_matches(self.transaction["transaction_id"], "match-user"))
        self.assertEqual(original, self.store.bank_transaction(self.transaction["transaction_id"], "match-user"))

    def test_confirmation_uses_recomputed_server_score_and_preserves_raw_transaction(self):
        proposal = {"source_type": "contract", "source_id": "current-contract", "score": 85}
        original = self.store.bank_transaction(self.transaction["transaction_id"], "match-user")
        with patch("app.finance_web.proposals_for_transaction", return_value=[proposal]) as recompute:
            response = self.client.post(
                self.url,
                data={
                    "source_type": "contract", "source_id": "current-contract",
                    "score": "1", "allocated_amount": "50.00",
                },
                headers={"X-CSRF-Token": "m" * 40},
            )
        self.assertEqual(302, response.status_code)
        recompute.assert_called_once()
        matches = self.store.transaction_matches(self.transaction["transaction_id"], "match-user")
        self.assertEqual(1, len(matches))
        self.assertEqual(85, matches[0]["score"])
        self.assertEqual(5000, matches[0]["allocated_cents"])
        self.assertEqual(original, self.store.bank_transaction(self.transaction["transaction_id"], "match-user"))


if __name__ == "__main__":
    unittest.main()
