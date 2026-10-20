"""Admin and locality contracts for the finance browser setting."""
from __future__ import annotations

import json
import os
import tempfile
import time
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

    def post_setting(
        self, client, value, *, token="a" * 40, remote_addr="127.0.0.1",
        timeout_seconds=None, import_limit_mib=None, staging_ttl_hours=None,
    ):
        data = {"enabled": value}
        if timeout_seconds is not None:
            data["timeout_seconds"] = timeout_seconds
        if import_limit_mib is not None:
            data["import_limit_mib"] = import_limit_mib
        if staging_ttl_hours is not None:
            data["staging_ttl_hours"] = staging_ttl_hours
        return client.post(
            "/documents/business/bookkeeping/finances/settings/bank-browser",
            data=data,
            headers={"X-CSRF-Token": token},
            environ_overrides={"REMOTE_ADDR": remote_addr},
        )

    def test_default_admin_setting_is_audited_and_persists(self):
        store = SettingsStore(self.root)
        self.assertIs(store.settings()["finance"]["bank_browser_enabled"], True)
        self.assertEqual(300, store.settings()["finance"]["bank_browser_timeout_seconds"])
        self.assertEqual(25 * 1024 * 1024, store.settings()["finance"]["statement_import_limit_bytes"])
        self.assertEqual(24 * 60 * 60, store.settings()["finance"]["statement_staging_ttl_seconds"])

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
        self.assertEqual("finance", row["target_id"])
        self.assertIs(json.loads(row["detail"])["enabled"], False)

    def test_browser_timeout_setting_is_persistent_bounded_and_used_for_export(self):
        updated = self.post_setting(self.admin, "1", timeout_seconds="900")
        self.assertEqual(302, updated.status_code)
        self.assertEqual(
            900,
            SettingsStore(self.root).settings()["finance"]["bank_browser_timeout_seconds"],
        )

        page = self.admin.get("/documents/business/bookkeeping/finances")
        self.assertEqual(200, page.status_code)
        self.assertIn('id="bank-browser-timeout"', page.get_data(as_text=True))
        self.assertIn('value="900"', page.get_data(as_text=True))

        for invalid in ("29", "1801", "1.5", "1e2", "9" * 5000):
            with self.subTest(timeout=invalid):
                self.assertEqual(
                    400,
                    self.post_setting(self.admin, "1", timeout_seconds=invalid).status_code,
                )
        self.assertEqual(
            900,
            SettingsStore(self.root).settings()["finance"]["bank_browser_timeout_seconds"],
        )
        with app.app_context():
            row = database.get_db().execute(
                "SELECT detail FROM security_event WHERE action=?",
                ("finance_bank_browser_setting_updated",),
            ).fetchone()
        self.assertEqual(900, json.loads(row["detail"])["timeout_seconds"])

        with patch("app.finance_web.capture_santander_statement", side_effect=TimeoutError) as capture:
            response = self.admin.post(
                "/documents/business/bookkeeping/finances/bank-browser/santander",
                data={"timeout_seconds": "30"},
                headers={"X-CSRF-Token": "a" * 40},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
        self.assertEqual(302, response.status_code)
        capture.assert_called_once_with(timeout_seconds=900)

    def test_import_limit_and_staging_ttl_are_persisted_enforced_and_bounded(self):
        response = self.post_setting(
            self.admin,
            "1",
            timeout_seconds="900",
            import_limit_mib="3",
            staging_ttl_hours="48",
        )
        self.assertEqual(302, response.status_code)
        persisted = SettingsStore(self.root).settings()["finance"]
        self.assertEqual(3 * 1024 * 1024, persisted["statement_import_limit_bytes"])
        self.assertEqual(48 * 60 * 60, persisted["statement_staging_ttl_seconds"])
        with app.app_context():
            audit_row = database.get_db().execute(
                "SELECT detail FROM security_event WHERE action=?",
                ("finance_bank_browser_setting_updated",),
            ).fetchone()
        audit_detail = json.loads(audit_row["detail"])
        self.assertEqual(3 * 1024 * 1024, audit_detail["import_limit_bytes"])
        self.assertEqual(48 * 60 * 60, audit_detail["staging_ttl_seconds"])

        page = self.admin.get("/documents/business/bookkeeping/finances")
        body = page.get_data(as_text=True)
        self.assertIn('value="3" min="1" max="25"', body)
        self.assertIn('value="48" min="1" max="168"', body)
        self.assertIn('name="reset" value="1"', body)
        self.assertIn('name="reset" value="1"', body)

        invalid_updates = (
            {"import_limit_mib": "0"},
            {"import_limit_mib": "26"},
            {"import_limit_mib": "9" * 5000},
            {"staging_ttl_hours": "0"},
            {"staging_ttl_hours": "169"},
            {"staging_ttl_hours": "1.5"},
        )
        for values in invalid_updates:
            with self.subTest(values=values):
                self.assertEqual(
                    400,
                    self.post_setting(self.admin, "1", timeout_seconds="900", **values).status_code,
                )

        from app.finance_web import _read_stage, _stage_dir, _stage_upload

        with app.app_context():
            with self.assertRaisesRegex(ValueError, "3 MiB"):
                _stage_upload(b"x" * (3 * 1024 * 1024 + 1), "finance-admin")
            token = _stage_upload(b"statement", "finance-admin")
            stage_path = _stage_dir("finance-admin") / f"{token}.bin"
            expired_at = time.time() - persisted["statement_staging_ttl_seconds"] - 1
            os.utime(stage_path, (expired_at, expired_at))
            with self.assertRaisesRegex(ValueError, "abgelaufen"):
                _read_stage(token, "finance-admin")
            self.assertFalse(stage_path.exists())

        reset = self.admin.post(
            "/documents/business/bookkeeping/finances/settings/bank-browser",
            data={"reset": "1"},
            headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, reset.status_code)
        defaults = SettingsStore(self.root).settings()["finance"]
        self.assertEqual(300, defaults["bank_browser_timeout_seconds"])
        self.assertEqual(25 * 1024 * 1024, defaults["statement_import_limit_bytes"])
        self.assertEqual(24 * 60 * 60, defaults["statement_staging_ttl_seconds"])
        reset_page = self.admin.get("/documents/business/bookkeeping/finances")
        reset_body = reset_page.get_data(as_text=True)
        self.assertIn('value="300" min="30" max="1800"', reset_body)
        self.assertIn('value="25" min="1" max="25"', reset_body)
        self.assertIn('value="24" min="1" max="168"', reset_body)

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
        with self.assertRaisesRegex(ValueError, "bank browser timeout"):
            SettingsStore(self.root).save(
                {"finance": {"bank_browser_timeout_seconds": True}}, "finance-admin",
            )
        with self.assertRaisesRegex(ValueError, "finance import limit"):
            SettingsStore(self.root).save(
                {"finance": {"statement_import_limit_bytes": 26 * 1024 * 1024}}, "finance-admin",
            )
        with self.assertRaisesRegex(ValueError, "finance staging TTL"):
            SettingsStore(self.root).save(
                {"finance": {"statement_staging_ttl_seconds": 59}}, "finance-admin",
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
