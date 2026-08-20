import json
import tempfile
import unittest
from pathlib import Path

from app import app
from app.calendar_collections import CalendarCollections
from app.contact_store import ContactStore
from app.db import ensure_auth_database
from app.webdav import authenticate_password


class FirstRunSetupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.root = Path(self.temp.name) / "documents"
        app.config.update(TESTING=True, DATABASE=str(Path(self.temp.name) / "users.sqlite"), DOCUMENT_ROOT=str(self.root))
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        self.base_url = "https://office.example"
        self.client.post("/auth/register", data={"username": "jens", "password": "browser-passwort"}, base_url=self.base_url)
        self.client.post("/auth/login", data={"username": "jens", "password": "browser-passwort"}, base_url=self.base_url)

    def tearDown(self):
        app.config.update(self.previous)
        self.temp.cleanup()

    def test_dashboard_and_setup_explain_services_and_acl(self):
        dashboard = self.client.get("/documents/dashboard", base_url=self.base_url).get_data(as_text=True)
        response = self.client.get("/documents/setup", base_url=self.base_url)
        body = response.get_data(as_text=True)

        self.assertIn("Erststart beginnen", dashboard)
        self.assertEqual(200, response.status_code)
        self.assertIn("https://office.example/webdav/files/jens/", body)
        self.assertIn("https://office.example/caldav/calendars/jens/", body)
        self.assertIn("https://office.example/carddav/addressbooks/jens/contacts/", body)
        self.assertIn("Lesen", body)
        self.assertIn("Schreiben", body)
        self.assertIn("Verwalten", body)

    def test_remote_http_disables_secret_creation(self):
        client = app.test_client()
        client.post("/auth/login", data={"username": "jens", "password": "browser-passwort"}, base_url="http://office.example")
        body = client.get("/documents/setup", base_url="http://office.example").get_data(as_text=True)
        self.assertIn("HTTPS fehlt", body)
        self.assertIn("disabled", body)
        denied = client.post("/documents/setup/access", base_url="http://office.example")
        self.assertEqual(400, denied.status_code)
        self.assertFalse((self.root / ".simpleoffice-meta" / "webdav-credentials.json").exists())

    def test_provisioning_creates_separate_working_secrets_once(self):
        response = self.client.post("/documents/setup/access", base_url=self.base_url)
        body = response.get_data(as_text=True)
        inputs = body.split('readonly value="')[1:4]
        passwords = [item.split('"', 1)[0] for item in inputs]

        self.assertEqual(200, response.status_code)
        self.assertEqual(3, len(passwords))
        self.assertEqual(3, len(set(passwords)))
        with app.test_request_context(base_url="https://office.example"):
            self.assertIsNotNone(authenticate_password("jens", passwords[0]))
        self.assertTrue(CalendarCollections(self.root).authenticate("jens", passwords[1]))
        self.assertTrue(ContactStore(self.root).carddav_authenticate("jens", passwords[2]))

        stored = (self.root / ".simpleoffice-meta" / "webdav-credentials.json").read_text(encoding="utf-8")
        self.assertNotIn(passwords[0], stored)
        later = self.client.get("/documents/setup", base_url=self.base_url).get_data(as_text=True)
        for password in passwords:
            self.assertNotIn(password, later)

    def test_completion_is_per_user_audited_and_export_has_no_passwords(self):
        response = self.client.post("/documents/setup/complete", data={"platform": "linux"}, base_url=self.base_url)
        self.assertEqual(302, response.status_code)
        state = json.loads((self.root / ".simpleoffice-meta" / "setup.json").read_text(encoding="utf-8"))
        self.assertEqual("linux", state["users"]["jens"]["platform"])
        dashboard = self.client.get("/documents/dashboard", base_url=self.base_url).get_data(as_text=True)
        self.assertNotIn("Erststart beginnen", dashboard)

        exported = self.client.get("/documents/setup/export.txt", base_url=self.base_url)
        body = exported.get_data(as_text=True)
        self.assertEqual(200, exported.status_code)
        self.assertIn("attachment; filename=SimpleOffice-Einrichtung.txt", exported.headers["Content-Disposition"])
        self.assertIn("sshfs -p 2222 jens@office.example:/", body)
        self.assertIn("keine Passwörter", body)


if __name__ == "__main__":
    unittest.main()
