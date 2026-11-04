"""Local federation identity store, profile fields and admin identity UI (no environment)."""
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from werkzeug.security import generate_password_hash

from app import app, app_config_store, db as database
from app import federation_local_identity as identity
from app.federation_store import FederationStore


class LocalIdentityStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.flask = Flask(__name__)
        self.flask.config.update(SECRET_KEY="local-identity-test-secret")
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

    def test_peer_id_falls_back_to_hostname(self):
        self.assertEqual(socket.gethostname(), identity.peer_id())

    def test_profile_fields_roundtrip(self):
        identity.save({
            "peer_id": "office-a",
            "label": "Office A",
            "country": "de",
            "public_url": "https://office-a.example/simpleoffice",
            "public_key": "public-key-material",
            "fingerprint": "sha256:test",
        })
        self.assertEqual("office-a", identity.peer_id())
        self.assertEqual("Office A", identity.label())
        self.assertEqual("DE", identity.country())
        self.assertEqual("https://office-a.example/simpleoffice", identity.public_url())
        self.assertEqual("public-key-material", identity.public_key())
        self.assertEqual("sha256:test", identity.fingerprint())
        status = identity.status()
        self.assertEqual("Office A", status["label"])
        self.assertFalse(status["token_configured"])
        self.assertFalse(status["directory_token_configured"])

    def test_country_validation(self):
        for bad in ("D", "DEU", "1A", "A ", "12"):
            with self.subTest(country=bad), self.assertRaises(ValueError):
                identity.validate({"peer_id": "x", "country": bad})
        with self.assertRaises(ValueError) as german:
            identity.validate({"peer_id": "x", "country": "USA"})
        self.assertIn("Profil", str(german.exception))
        with self.assertRaises(ValueError) as english:
            identity.validate({"peer_id": "x", "country": "USA"}, "en")
        self.assertIn("profile", str(english.exception).lower())

    def test_token_roundtrip_and_status_hides_values(self):
        identity.save({"peer_id": "office-a", "token": "shared-secret", "directory_token": "dir-secret"})
        self.assertEqual("shared-secret", identity.shared_token())
        self.assertEqual("dir-secret", identity.directory_token())
        status = identity.status()
        self.assertTrue(status["token_configured"])
        self.assertTrue(status["directory_token_configured"])
        self.assertNotIn("shared-secret", json.dumps(status))
        self.assertNotIn("dir-secret", json.dumps(status))

    def test_empty_token_discards_stored_secret(self):
        identity.save({"peer_id": "office-a", "token": "shared-secret"})
        self.assertTrue(identity.token_configured())
        identity.save({"peer_id": "office-a", "token": ""})
        self.assertFalse(identity.token_configured())
        self.assertEqual("", identity.shared_token())

    def test_rotate_token_returns_fresh_secret(self):
        first = identity.rotate_token()
        second = identity.rotate_token()
        self.assertNotEqual(first, second)
        self.assertEqual(second, identity.shared_token())
        self.assertTrue(identity.token_configured())


class LocalIdentityAdminTests(unittest.TestCase):
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
                ("identity-admin", generate_password_hash("identity-admin-password")),
            )
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES (?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("identity-user", generate_password_hash("identity-user-password")),
            )
            db.commit()
        self.client = app.test_client()
        response = self.client.post(
            "/auth/login",
            data={"username": "identity-admin", "password": "identity-admin-password"},
        )
        self.assertLess(response.status_code, 400)
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "de"

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_dashboard_shows_identity_section_without_token_values(self):
        with app.app_context():
            identity.save({"peer_id": "office-a", "token": "hidden-shared"})
        response = self.client.get("/admin/federation/peer-discovery")
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn('id="local-identity"', body)
        self.assertIn("Server-Token", body)
        self.assertIn("Hinterlegt", body)
        self.assertNotIn("hidden-shared", body)

    def test_save_identity_stores_profile_and_token_marker(self):
        response = self.client.post(
            "/admin/federation/peer-discovery/identity",
            data={
                "peer_id": "office-a",
                "label": "Office A",
                "country": "de",
                "public_url": "https://office-a.example/simpleoffice",
                "public_key": "public-key-material",
                "fingerprint": "sha256:test",
                "token": "saved-shared",
                "directory_token": "",
            },
        )
        self.assertEqual(302, response.status_code)
        self.assertIn("#local-identity", response.headers["Location"])
        with app.app_context():
            self.assertEqual("Office A", identity.label())
            self.assertEqual("DE", identity.country())
            self.assertEqual("saved-shared", identity.shared_token())
            self.assertFalse(identity.directory_token_configured())

    def test_save_identity_rejects_bad_country(self):
        response = self.client.post(
            "/admin/federation/peer-discovery/identity",
            data={"peer_id": "office-a", "country": "USA"},
        )
        self.assertEqual(302, response.status_code)
        with app.app_context():
            self.assertEqual("", identity.country())

    def test_rotate_token_shows_secret_once_without_storing_it_in_events(self):
        response = self.client.post("/admin/federation/peer-discovery/identity/rotate-token")
        self.assertEqual(200, response.status_code)
        self.assertEqual("no-store", response.headers["Cache-Control"])
        body = response.get_data(as_text=True)
        with app.app_context():
            fresh = identity.shared_token()
        self.assertTrue(fresh)
        self.assertIn(fresh, body)
        events = FederationStore(self.root).events(20)
        self.assertTrue(any(event["action"] == "identity_token_rotated" for event in events))
        self.assertNotIn(fresh, json.dumps(events))

    def test_identity_routes_need_admin(self):
        user_client = app.test_client()
        user_client.post(
            "/auth/login",
            data={"username": "identity-user", "password": "identity-user-password"},
        )
        for path in (
            "/admin/federation/peer-discovery/identity",
            "/admin/federation/peer-discovery/identity/rotate-token",
        ):
            response = user_client.post(path, data={})
            self.assertEqual(403, response.status_code)


if __name__ == "__main__":
    unittest.main()
