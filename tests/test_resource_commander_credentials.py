"""Resource Commander credentials in the disk store + admin UI (no environment)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app import app, app_config_store, db as database
from app import resource_commander_credentials as credentials


class CommanderCredentialsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(
            app_config_store, "settings_path",
            return_value=Path(self.temp.name) / "app-config.json",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.flask = Flask(__name__)
        self.flask.config.update(SECRET_KEY="commander-credentials-test")
        self.context = self.flask.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)

    def test_unset_tokens_report_not_configured(self):
        self.assertEqual("", credentials.inbound_token())
        self.assertFalse(credentials.inbound_token_configured())
        self.assertEqual({}, credentials.peer_tokens())
        self.assertFalse(credentials.peer_tokens_configured())

    def test_save_encrypts_and_never_returns_plaintext(self):
        mapping = json.dumps({"peer-a": "peer-secret-1"})
        status = credentials.save({"token": "inbound-secret-1", "peer_tokens": mapping})
        self.assertTrue(status["token_configured"])
        self.assertTrue(status["peer_tokens_configured"])
        self.assertEqual("inbound-secret-1", credentials.inbound_token())
        self.assertEqual("peer-secret-1", credentials.peer_commander_token("peer-a"))
        self.assertEqual("", credentials.peer_commander_token("unknown-peer"))
        raw = (Path(self.temp.name) / "app-config.json").read_text(encoding="utf-8")
        self.assertNotIn("inbound-secret-1", raw)
        self.assertNotIn("peer-secret-1", raw)

    def test_empty_fields_keep_stored_secrets(self):
        credentials.save({"token": "inbound-secret-1"})
        status = credentials.save({"token": "", "peer_tokens": ""})
        self.assertTrue(status["token_configured"])
        self.assertEqual("inbound-secret-1", credentials.inbound_token())

    def test_rotate_replaces_inbound_token(self):
        credentials.save({"token": "inbound-secret-1"})
        fresh = credentials.rotate_token()
        self.assertTrue(fresh)
        self.assertNotEqual("inbound-secret-1", fresh)
        self.assertEqual(fresh, credentials.inbound_token())

    def test_invalid_values_fail_bilingual(self):
        with self.assertRaises(ValueError) as german:
            credentials.save({"token": "nö\nbroken"})
        self.assertIn("Commander", str(german.exception))
        with self.assertRaises(ValueError) as english:
            credentials.save({"peer_tokens": "no-json"}, "en")
        self.assertIn("peer mapping", str(english.exception).lower())


class CommanderCredentialsAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        self.root.mkdir(parents=True, exist_ok=True)
        saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        self.addCleanup(lambda: app.config.update(saved))
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(self.base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        patcher = patch.object(
            app_config_store, "settings_path",
            return_value=self.base / "app-config.json",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post(
            "/auth/register",
            data={"username": "admin", "password": "synthetic-commander-password"},
        )
        self.client.post(
            "/auth/login",
            data={"username": "admin", "password": "synthetic-commander-password"},
        )

    def test_admin_can_save_tokens_and_sees_only_presence(self):
        response = self.client.post(
            "/admin/federation/commander",
            data={
                "commander_token": "inbound-secret-1",
                "commander_peer_tokens": json.dumps({"peer-a": "peer-secret-1"}),
            },
        )
        self.assertEqual(302, response.status_code)
        self.assertIn("#resource-commander", response.headers["Location"])
        with app.app_context():
            self.assertTrue(credentials.inbound_token_configured())
            self.assertTrue(credentials.peer_tokens_configured())
        page = self.client.get("/admin/federation/peer-discovery")
        self.assertEqual(200, page.status_code)
        body = page.get_data(as_text=True)
        self.assertIn('id="resource-commander"', body)
        self.assertNotIn("inbound-secret-1", body)
        self.assertNotIn("peer-secret-1", body)

    def test_rotate_invalidates_old_token_immediately(self):
        self.client.post(
            "/admin/federation/commander", data={"commander_token": "old-secret"},
        )
        response = self.client.post("/admin/federation/commander/rotate")
        self.assertEqual(200, response.status_code)
        self.assertEqual("no-store", response.headers["Cache-Control"])
        with app.app_context():
            self.assertNotEqual("old-secret", credentials.inbound_token())
            self.assertTrue(credentials.inbound_token_configured())
        self.assertNotIn("old-secret", response.get_data(as_text=True))

    def test_non_admin_cannot_save_or_rotate(self):
        with app.app_context():
            conn = database.get_db()
            conn.execute("UPDATE user SET is_admin=0 WHERE username='admin'")
            conn.commit()
        self.assertEqual(
            403,
            self.client.post(
                "/admin/federation/commander", data={"commander_token": "x"},
            ).status_code,
        )
        self.assertEqual(
            403, self.client.post("/admin/federation/commander/rotate").status_code,
        )


if __name__ == "__main__":
    unittest.main()
