"""HTTP contracts for explicit, read-only FinTS balance requests."""
from __future__ import annotations

import base64
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app, db as database
from app.finance_fints import FinTSAuthenticationRequired, FinTSFeatureUnavailable, FinTSUnavailable
from app.finance_store import FinanceStore
from app.finance_store import FINTS_CHALLENGE_TTL_SECONDS


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

    def test_account_discovery_displays_read_capabilities_and_tan_methods_without_caching(self):
        remote = {
            "remote_account_id": "iban:DE02120300000000202051",
            "iban": "DE02120300000000202051", "account_number": "202051",
            "account_type": "Girokonto", "currency": "EUR",
        }
        capabilities = {
            "read": {"balance": True, "transactions": False},
            "accounts": {remote["remote_account_id"]: {"balance": True, "transactions": False}},
            "tan_mechanisms": [{"security_function": "942", "name": "pushTAN",
                                "version": 7, "tan_process": "2"}],
            "current_tan_mechanism": "942",
        }
        url = self.url.rsplit("/", 1)[0] + "/discover"
        with patch("app.finance_web.discover_accounts_with_capabilities", return_value=([remote], capabilities)) as discover:
            response = self.client.post(
                url, data={"pin": "request-pin"}, headers={"X-CSRF-Token": "f" * 40},
            )

        body = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("Von der Bank gemeldete Lese- und TAN-Fähigkeiten", body)
        self.assertIn("pushTAN", body)
        self.assertIn("nicht unterstützt", body)
        self.assertNotIn("request-pin", body)
        self.assertEqual("no-store", response.headers["Cache-Control"])
        discover.assert_called_once()

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

    def test_unsupported_bank_balance_is_reported_as_a_capability_not_a_bad_pin(self):
        with patch(
            "app.finance_web.fetch_balance",
            side_effect=FinTSFeatureUnavailable("Die Bank unterstützt den Lesezugriff auf Saldoabfrage für dieses Konto nicht."),
        ) as fetch:
            response = self.post_balance()
        self.assertEqual(302, response.status_code)
        fetch.assert_called_once()
        with self.client.session_transaction() as session:
            message = " ".join(str(item) for item in session.get("_flashes", []))
        self.assertIn("unterstützt", message)
        self.assertNotIn("PIN", message)

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

    def test_tan_challenge_can_be_resumed_once_with_csrf_and_owner_binding(self):
        challenge_state = {
            "version": 1,
            "operation": "balance",
            "step": "balance",
            "client_data": base64.b64encode(b"client-state").decode("ascii"),
            "dialog_data": base64.b64encode(b"dialog-state").decode("ascii"),
            "challenge_data": base64.b64encode(b"retry-state").decode("ascii"),
            "remote_account_id": self.mapping["remote_account_id"],
            "account_currency": "EUR",
            "challenge_text": "Approve in your bank app",
            "decoupled": False,
        }
        challenge_error = FinTSAuthenticationRequired(
            "TAN required", challenge_state=challenge_state,
            challenge_text=challenge_state["challenge_text"],
        )
        with patch("app.finance_web.fetch_balance", side_effect=challenge_error):
            challenge_response = self.post_balance()

        self.assertEqual(200, challenge_response.status_code)
        self.assertEqual("no-store", challenge_response.headers["Cache-Control"])
        self.assertIn("Approve in your bank app", challenge_response.get_data(as_text=True))
        token_match = re.search(
            r'name="challenge_token" value="([A-Za-z0-9_-]{43})"',
            challenge_response.get_data(as_text=True),
        )
        self.assertIsNotNone(token_match)
        token = token_match.group(1)

        with patch(
            "app.finance_web.fetch_balance",
            return_value={
                "remote_account_id": self.mapping["remote_account_id"],
                "booked_balance": "8.90", "currency": "EUR",
                "available_balance": "", "available_currency": "",
            },
        ) as resume:
            response = self.client.post(
                self.url + "/challenge",
                data={"challenge_token": token, "pin": "temporary-pin", "tan": "123456"},
                headers={"X-CSRF-Token": "f" * 40},
            )
        self.assertEqual(200, response.status_code)
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertIn("8.90 EUR", response.get_data(as_text=True))
        resume.assert_called_once_with(
            self.connection, self.mapping["remote_account_id"], "temporary-pin",
            challenge_state=challenge_state, tan="123456",
        )
        with app.app_context():
            with self.assertRaisesRegex(ValueError, "ungültig oder abgelaufen"):
                self.store.consume_fints_challenge(
                    token, self.connection["connection_id"], "balance-user",
                )

    def test_account_discovery_tan_challenge_can_be_resumed_once(self):
        challenge_state = {
            "version": 1,
            "operation": "balance",
            "step": "accounts",
            "client_data": base64.b64encode(b"client-state").decode("ascii"),
            "dialog_data": base64.b64encode(b"dialog-state").decode("ascii"),
            "challenge_data": base64.b64encode(b"retry-state").decode("ascii"),
            "remote_account_id": self.mapping["remote_account_id"],
            "account_currency": "EUR",
            "challenge_text": "Freigabe zur Kontenauswahl",
            "decoupled": False,
        }
        challenge_error = FinTSAuthenticationRequired(
            "Account list requires TAN", challenge_state=challenge_state,
            challenge_text=challenge_state["challenge_text"],
        )
        with patch("app.finance_web.fetch_balance", side_effect=challenge_error):
            challenge_response = self.post_balance()

        self.assertEqual(200, challenge_response.status_code)
        self.assertIn("Freigabe zur Kontenauswahl", challenge_response.get_data(as_text=True))
        token_match = re.search(
            r'name="challenge_token" value="([A-Za-z0-9_-]{43})"',
            challenge_response.get_data(as_text=True),
        )
        self.assertIsNotNone(token_match)
        token = token_match.group(1)

        with patch(
            "app.finance_web.fetch_balance",
            return_value={
                "remote_account_id": self.mapping["remote_account_id"],
                "booked_balance": "19.75", "currency": "EUR",
                "available_balance": "", "available_currency": "",
            },
        ) as resume:
            response = self.client.post(
                self.url + "/challenge",
                data={"challenge_token": token, "pin": "temporary-pin", "tan": "654321"},
                headers={"X-CSRF-Token": "f" * 40},
            )

        self.assertEqual(200, response.status_code)
        self.assertIn("19.75 EUR", response.get_data(as_text=True))
        resume.assert_called_once_with(
            self.connection, self.mapping["remote_account_id"], "temporary-pin",
            challenge_state=challenge_state, tan="654321",
        )
        self.assertEqual([], self.store.bank_transactions(self.account["account_id"], "balance-user"))

    def test_missing_fints_product_registration_is_explained_instead_of_swallowed(self):
        with patch(
            "app.finance_web.fetch_balance",
            side_effect=FinTSUnavailable("FinTS-Produkt-ID fehlt; SIMPLEOFFICE_FINTS_PRODUCT_ID prüfen."),
        ):
            response = self.client.post(
                self.url,
                data={"remote_account_id": self.mapping["remote_account_id"], "pin": "temporary-pin"},
                headers={"X-CSRF-Token": "f" * 40},
                follow_redirects=True,
            )

        self.assertEqual(200, response.status_code)
        self.assertIn("SIMPLEOFFICE_FINTS_PRODUCT_ID prüfen", response.get_data(as_text=True))
        self.assertNotIn("temporary-pin", response.get_data(as_text=True))

    def test_sca_challenge_state_is_encrypted_owner_bound_and_single_use(self):
        payload = {
            "version": 1,
            "operation": "balance",
            "step": "balance",
            "client_data": base64.b64encode(b"opaque-client-state").decode("ascii"),
            "dialog_data": base64.b64encode(b"opaque-dialog-state").decode("ascii"),
            "challenge_data": base64.b64encode(b"opaque-challenge-state").decode("ascii"),
            "remote_account_id": self.mapping["remote_account_id"],
            "account_currency": "EUR",
            "challenge_text": "Approve the read-only balance request in your bank app.",
            "decoupled": True,
        }
        with app.app_context():
            token = self.store.create_fints_challenge(
                self.connection["connection_id"], payload, "balance-user", now=100,
            )
            with self.store._db() as finance_db:
                row = finance_db.execute(
                    "SELECT token_hash,payload FROM finance_fints_challenge",
                ).fetchone()

        self.assertEqual(64, len(row["token_hash"]))
        self.assertTrue(row["payload"].startswith("enc:v1:"))
        self.assertNotIn("Approve the read-only", row["payload"])
        self.assertNotIn("temporary-pin", row["payload"])
        self.assertNotIn(token, row["token_hash"])

        with app.app_context():
            with self.assertRaisesRegex(ValueError, "ungültig oder abgelaufen"):
                self.store.consume_fints_challenge(
                    token, self.connection["connection_id"], "another-user", now=101,
                )
            restored = self.store.consume_fints_challenge(
                token, self.connection["connection_id"], "balance-user", now=101,
            )
        self.assertEqual(payload, restored)
        with app.app_context():
            with self.assertRaisesRegex(ValueError, "ungültig oder abgelaufen"):
                self.store.consume_fints_challenge(
                    token, self.connection["connection_id"], "balance-user", now=102,
                )

    def test_sca_challenge_expires_and_payload_rejects_pin_or_tan_fields(self):
        payload = {
            "version": 1,
            "operation": "balance",
            "step": "balance",
            "client_data": "Y2xpZW50",
            "dialog_data": "ZGlhbG9n",
            "challenge_data": "Y2hhbGxlbmdl",
            "remote_account_id": self.mapping["remote_account_id"],
            "account_currency": "EUR",
            "challenge_text": "Confirm in the bank app.",
            "decoupled": True,
        }
        with app.app_context():
            with self.assertRaisesRegex(ValueError, "PIN und TAN"):
                self.store.create_fints_challenge(
                    self.connection["connection_id"], {**payload, "pin": "must-not-persist"},
                    "balance-user", now=100,
                )
            token = self.store.create_fints_challenge(
                self.connection["connection_id"], payload, "balance-user", now=100,
            )
            with self.assertRaisesRegex(ValueError, "ungültig oder abgelaufen"):
                self.store.consume_fints_challenge(
                    token, self.connection["connection_id"], "balance-user",
                    now=100 + FINTS_CHALLENGE_TTL_SECONDS,
                )
            with self.store._db() as finance_db:
                count = finance_db.execute(
                    "SELECT COUNT(*) AS count FROM finance_fints_challenge",
                ).fetchone()["count"]
        self.assertEqual(0, count)


if __name__ == "__main__":
    unittest.main()
