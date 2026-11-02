"""Marketplace and OAuth credentials in the disk store + admin UI (no environment)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from werkzeug.security import generate_password_hash

from app import app, app_config_store, db as database
from app import marketplace_credentials as store


class MarketplaceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.flask = Flask(__name__)
        self.flask.config.update(SECRET_KEY="marketplace-test-secret")
        self.context = self.flask.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        patcher = patch.object(
            app_config_store, "settings_path", return_value=self.root / "app-config.json",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temp.cleanup()

    def test_defaults_are_unconfigured(self):
        self.assertEqual(("", "", ""), store.amazon_credentials())
        self.assertEqual(("", ""), store.ebay_credentials())
        self.assertEqual("EBAY_DE", store.ebay_marketplace_id())
        self.assertEqual(("", ""), store.google_oauth_credentials())
        self.assertEqual((), store.google_oauth_redirect_uris())
        self.assertEqual(128, store.google_drive_max_mib())
        self.assertFalse(store.amazon_secret_configured())

    def test_credentials_roundtrip_without_showing_secrets(self):
        store.save({
            "amazon_access_key": "AKID",
            "amazon_secret_key": "amazon-secret",
            "amazon_partner_tag": "tag-21",
            "ebay_client_id": "ebay-id",
            "ebay_client_secret": "ebay-secret",
            "ebay_marketplace_id": "EBAY_US",
            "google_client_id": "google-id",
            "google_client_secret": "google-secret",
            "google_credentials_file": "",
            "google_calendar_accounts_json": json.dumps({"alice": {"calendar_id": "primary"}}),
            "google_drive_max_mib": "64",
        })
        self.assertEqual(("AKID", "amazon-secret", "tag-21"), store.amazon_credentials())
        self.assertEqual(("ebay-id", "ebay-secret"), store.ebay_credentials())
        self.assertEqual("EBAY_US", store.ebay_marketplace_id())
        self.assertEqual(("google-id", "google-secret"), store.google_oauth_credentials())
        self.assertEqual(64, store.google_drive_max_mib())
        self.assertEqual(
            json.dumps({"alice": {"calendar_id": "primary"}}),
            store.google_calendar_accounts_raw(),
        )
        status = store.status()
        self.assertTrue(status["amazon_secret_configured"])
        self.assertTrue(status["ebay_secret_configured"])
        self.assertTrue(status["google_secret_configured"])
        self.assertTrue(status["google_accounts_configured"])
        self.assertNotIn("amazon-secret", json.dumps(status))
        self.assertNotIn("ebay-secret", json.dumps(status))
        self.assertNotIn("google-secret", json.dumps(status))

    def test_empty_secrets_keep_stored_values(self):
        store.save({"amazon_secret_key": "amazon-secret", "ebay_client_secret": "ebay-secret"})
        store.save({"amazon_secret_key": "", "ebay_client_secret": ""})
        self.assertEqual("amazon-secret", store.amazon_credentials()[1])
        self.assertEqual("ebay-secret", store.ebay_credentials()[1])

    def test_oauth_file_fills_empty_store_values(self):
        with tempfile.TemporaryDirectory() as temp:
            credentials_file = Path(temp) / "google-client.json"
            credentials_file.write_text(json.dumps({
                "web": {
                    "client_id": "json-client",
                    "client_secret": "json-secret",
                    "redirect_uris": ["https://office.example.test/auth/google/callback"],
                },
            }), encoding="utf-8")
            store.save({"google_credentials_file": str(credentials_file)})
        self.assertEqual(("json-client", "json-secret"), store.google_oauth_credentials())
        self.assertEqual(
            ("https://office.example.test/auth/google/callback",),
            store.google_oauth_redirect_uris(),
        )

    def test_invalid_values_fail_bilingual(self):
        with self.assertRaises(ValueError) as german:
            store.validate({"ebay_marketplace_id": "broken\nvalue"})
        self.assertIn("Anbindung", str(german.exception))
        with self.assertRaises(ValueError) as english:
            store.validate({"google_drive_max_mib": "9999"}, "en")
        self.assertIn("integration", str(english.exception).lower())
        with self.assertRaises(ValueError):
            store.validate({"google_calendar_accounts_json": "{not-json"})
        with self.assertRaises(ValueError):
            store.validate({"amazon_secret_key": "has\nnewline"})


class MarketplaceAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "documents"
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        self.root.mkdir(parents=True, exist_ok=True)
        patcher = patch.object(
            app_config_store, "settings_path", return_value=base / "app-config.json",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES (?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("market-admin", generate_password_hash("market-admin-password")),
            )
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES (?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("market-user", generate_password_hash("market-user-password")),
            )
            db.commit()
        self.client = app.test_client()
        response = self.client.post(
            "/auth/login",
            data={"username": "market-admin", "password": "market-admin-password"},
        )
        self.assertLess(response.status_code, 400)
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "de"

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_page_shows_markers_without_secrets(self):
        with app.app_context():
            store.save({"ebay_client_id": "ebay-id", "ebay_client_secret": "hidden-secret"})
        response = self.client.get("/admin/integrations/marketplace")
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("ebay-id", body)
        self.assertIn("Hinterlegt", body)
        self.assertNotIn("hidden-secret", body)

    def test_save_credentials(self):
        response = self.client.post(
            "/admin/integrations/marketplace",
            data={
                "amazon_access_key": "AKID",
                "amazon_secret_key": "amazon-secret",
                "amazon_partner_tag": "tag-21",
                "ebay_client_id": "ebay-id",
                "ebay_client_secret": "",
                "ebay_marketplace_id": "",
                "google_drive_max_mib": "64",
            },
        )
        self.assertEqual(302, response.status_code)
        with app.app_context():
            self.assertEqual("AKID", store.amazon_access_key())
            self.assertEqual("amazon-secret", store.amazon_credentials()[1])
            self.assertEqual("EBAY_DE", store.ebay_marketplace_id())
            self.assertEqual(64, store.google_drive_max_mib())

    def test_save_credentials_rejects_invalid(self):
        response = self.client.post(
            "/admin/integrations/marketplace",
            data={"google_drive_max_mib": "9999"},
        )
        self.assertEqual(302, response.status_code)
        with app.app_context():
            self.assertEqual(128, store.google_drive_max_mib())

    def test_marketplace_routes_need_admin(self):
        user_client = app.test_client()
        user_client.post(
            "/auth/login",
            data={"username": "market-user", "password": "market-user-password"},
        )
        self.assertEqual(403, user_client.get("/admin/integrations/marketplace").status_code)
        self.assertEqual(403, user_client.post("/admin/integrations/marketplace", data={}).status_code)


if __name__ == "__main__":
    unittest.main()
