"""Admin and locality contracts for the finance browser setting."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app, db as database
from app.settings_store import SettingsStore


class FinanceAdminSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = {
            key: app.config.get(key)
            for key in (
                "DATABASE", "DOCUMENT_ROOT", "SECRET_KEY", "TESTING",
                "TEST_CSRF_PROTECTION",
            )
        }
        app.config.update(
            DATABASE=str(self.root / "auth.sqlite3"),
            DOCUMENT_ROOT=str(self.root),
            SECRET_KEY="finance-admin-settings-test",
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            for username, is_admin in (("finance-admin", 1), ("finance-user", 0)):
                db.execute(
                    "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                    "VALUES(?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (username, generate_password_hash(username), is_admin),
                )
            db.commit()

        self.admin = app.test_client()
        self.user = app.test_client()
        self.admin.post(
            "/auth/login",
            data={"username": "finance-admin", "password": "finance-admin"},
        )
        self.user.post(
            "/auth/login",
            data={"username": "finance-user", "password": "finance-user"},
        )
        for client, token in ((self.admin, "a" * 40), (self.user, "u" * 40)):
            with client.session_transaction() as session:
                session["_csrf_token"] = token
        app.config["TEST_CSRF_PROTECTION"] = True

    def tearDown(self):
        app.config.update(self.config)
        self.temp.cleanup()

    def post_setting(self, client, value, *, token="a" * 40, remote_addr="127.0.0.1"):
        return client.post(
            "/documents/business/bookkeeping/finances/settings/bank-browser",
            data={"enabled": value},
            headers={"X-CSRF-Token": token},
            environ_overrides={"REMOTE_ADDR": remote_addr},
        )

    def test_default_admin_setting_is_audited_and_persists(self):
        store = SettingsStore(self.root)
        self.assertIs(store.settings()["finance"]["bank_browser_enabled"], True)

        response = self.post_setting(self.admin, "0")

        self.assertEqual(302, response.status_code)
        persisted = SettingsStore(self.root).settings()
        self.assertIs(persisted["finance"]["bank_browser_enabled"], False)
        with app.app_context():
            row = database.get_db().execute(
                "SELECT actor_name, action, target_type, target_id, detail "
                "FROM security_event WHERE action=?",
                ("finance_bank_browser_setting_updated",),
            ).fetchone()
        self.assertEqual("finance-admin", row["actor_name"])
        self.assertEqual("settings", row["target_type"])
        self.assertEqual("finance.bank_browser_enabled", row["target_id"])
        self.assertIs(json.loads(row["detail"])["enabled"], False)

    def test_non_admin_missing_csrf_and_invalid_values_are_rejected(self):
        self.assertEqual(403, self.post_setting(self.user, "0", token="u" * 40).status_code)
        self.assertEqual(403, self.admin.post(
            "/documents/business/bookkeeping/finances/settings/bank-browser",
            data={"enabled": "0"},
        ).status_code)
        self.assertEqual(400, self.post_setting(self.admin, "true").status_code)
        self.assertIs(
            SettingsStore(self.root).settings()["finance"]["bank_browser_enabled"],
            True,
        )

    def test_bank_browser_is_available_only_to_loopback_clients(self):
        from app.finance_web import _local_bank_browser_enabled

        with app.test_request_context(
            "/finances", environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        ):
            self.assertTrue(_local_bank_browser_enabled())
        with app.test_request_context(
            "/finances", environ_overrides={"REMOTE_ADDR": "192.0.2.10"},
        ):
            self.assertFalse(_local_bank_browser_enabled())
        with app.test_request_context(
            "/finances",
            base_url="https://office.example",
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        ):
            self.assertFalse(_local_bank_browser_enabled())

        with patch("app.finance_web.capture_santander_statement") as capture:
            response = self.admin.post(
                "/documents/business/bookkeeping/finances/bank-browser/santander",
                headers={"X-CSRF-Token": "a" * 40},
                environ_overrides={"REMOTE_ADDR": "192.0.2.10"},
            )
        self.assertEqual(403, response.status_code)
        capture.assert_not_called()

    def test_settings_store_rejects_non_boolean_finance_values(self):
        with self.assertRaisesRegex(ValueError, "bank browser setting"):
            SettingsStore(self.root).save(
                {"finance": {"bank_browser_enabled": "0"}}, "finance-admin",
            )

    def test_legacy_database_setting_is_migrated_without_losing_its_value(self):
        with app.app_context():
            db = database.get_db()
            db.execute(
                "CREATE TABLE finance_ui_settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            db.execute(
                "INSERT INTO finance_ui_settings(name,value) VALUES(?,?)",
                ("bank_browser_enabled", "0"),
            )
            db.commit()

        from app.finance_web import _bank_browser_setting

        with app.test_request_context("/finances"):
            self.assertFalse(_bank_browser_setting())
        self.assertIs(
            SettingsStore(self.root).settings()["finance"]["bank_browser_enabled"],
            False,
        )


if __name__ == "__main__":
    unittest.main()
