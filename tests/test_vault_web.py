import io
import tempfile
import unittest
from pathlib import Path

from flask import Flask, g

from app.password_vault import PasswordVault
from app.vault_web import bp


class VaultWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        template_root = Path(__file__).parents[1] / "templates"
        self.app = Flask(__name__, template_folder=str(template_root))
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="vault-web-test-secret",
            DOCUMENT_ROOT=str(self.root),
            VAULT_UNLOCK_SECONDS=300,
        )
        self.user = {"username": "alice", "is_admin": 0}
        self.app.before_request(lambda: setattr(g, "user", self.user))
        self.app.jinja_env.globals["csrf_token"] = lambda: "test-csrf"
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()
        self.password = "correct horse battery staple"

    def tearDown(self):
        self.temp.cleanup()

    def _setup(self):
        response = self.client.post(
            "/vault/setup",
            data={
                "master_password": self.password,
                "master_password_confirm": self.password,
            },
        )
        self.assertEqual(302, response.status_code)

    def _add(self, **values):
        payload = {
            "type": "login",
            "name": "Example",
            "url": "https://example.org/login",
            "username": "alice@example.org",
            "email": "alice@example.org",
            "password": "SuperSecret-123!",
            "notes": "private note",
            "totp": "JBSWY3DPEHPK3PXP",
            "tags": "work, admin",
        }
        payload.update(values)
        return self.client.post("/vault/entries", data=payload)

    def test_setup_keeps_vault_key_out_of_cookie_session(self):
        self._setup()
        with self.client.session_transaction() as session:
            self.assertIn("vault_unlock_token", session)
            serialized = repr(dict(session))
        self.assertNotIn(self.password, serialized)
        self.assertNotIn("SuperSecret", serialized)

    def test_search_projection_never_returns_secret_fields(self):
        self._setup()
        self.assertEqual(302, self._add().status_code)

        response = self.client.get("/vault/api/v1/search?q=example")
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(1, len(payload["entries"]))
        entry = payload["entries"][0]
        self.assertEqual("Example", entry["name"])
        self.assertNotIn("password", entry)
        self.assertNotIn("totp", entry)
        self.assertNotIn("notes", entry)
        self.assertEqual("private, no-store", response.headers["Cache-Control"])

        page = self.client.get("/vault/")
        self.assertNotIn(b"SuperSecret-123!", page.data)
        self.assertNotIn(b"private note", page.data)
        self.assertNotIn(b"JBSWY3DPEHPK3PXP", page.data)

    def test_explicit_reveal_returns_one_full_credential_and_no_store(self):
        self._setup()
        self._add()
        row = self.client.get("/vault/api/v1/search").get_json()["entries"][0]

        response = self.client.post(f"/vault/api/v1/credentials/{row['entry_id']}/reveal")
        self.assertEqual(200, response.status_code)
        credential = response.get_json()["credential"]["data"]
        self.assertEqual("SuperSecret-123!", credential["password"])
        self.assertEqual("JBSWY3DPEHPK3PXP", credential["totp"])
        self.assertEqual("private, no-store", response.headers["Cache-Control"])

    def test_lock_invalidates_browser_api(self):
        self._setup()
        self._add()
        self.client.post("/vault/lock")
        response = self.client.get("/vault/api/v1/search")
        self.assertEqual(423, response.status_code)
        self.assertFalse(response.get_json()["ok"])

    def test_browser_csv_import_never_overwrites_existing_match(self):
        self._setup()
        self._add(password="original-secret")
        raw = (
            "url,username,password\n"
            "https://example.org/login,alice@example.org,replacement-secret\n"
            "https://other.example/,bob,new-secret\n"
        ).encode("utf-8")

        preview = self.client.post(
            "/vault/import/browser-csv",
            data={"file": (io.BytesIO(raw), "passwords.csv")},
            content_type="multipart/form-data",
        )
        self.assertEqual(302, preview.status_code)

        imported = self.client.post(
            "/vault/import/browser-csv",
            data={"confirm": "1", "file": (io.BytesIO(raw), "passwords.csv")},
            content_type="multipart/form-data",
        )
        self.assertEqual(302, imported.status_code)

        vault = PasswordVault(self.root)
        key = vault.unlock("alice", self.password)
        entries = vault.entries("alice", key)
        self.assertEqual(2, len(entries))
        by_url = {row["data"].get("url"): row["data"] for row in entries}
        self.assertEqual("original-secret", by_url["https://example.org/login"]["password"])
        self.assertEqual("new-secret", by_url["https://other.example/"]["password"])

    def test_plaintext_export_requires_explicit_confirmation_and_no_store(self):
        self._setup()
        self._add()
        denied = self.client.post("/vault/export/browser-csv", data={"confirm": ""})
        self.assertEqual(302, denied.status_code)

        response = self.client.post("/vault/export/browser-csv", data={"confirm": "EXPORT"})
        self.assertEqual(200, response.status_code)
        self.assertIn(b"SuperSecret-123!", response.data)
        self.assertEqual("private, no-store", response.headers["Cache-Control"])
        self.assertIn("attachment", response.headers["Content-Disposition"])

    def test_encrypted_backup_export_does_not_contain_plaintext_secret(self):
        self._setup()
        self._add()
        response = self.client.post("/vault/export/backup")
        self.assertEqual(200, response.status_code)
        self.assertNotIn(b"SuperSecret-123!", response.data)
        self.assertNotIn(b"private note", response.data)
        self.assertEqual("private, no-store", response.headers["Cache-Control"])

    def test_navigation_and_templates_expose_vault_without_secret_search_fields(self):
        project = Path(__file__).parents[1]
        nav = (project / "templates" / "documents" / "nav.html").read_text(encoding="utf-8")
        index = (project / "templates" / "vault" / "index.html").read_text(encoding="utf-8")
        detail = (project / "templates" / "vault" / "entry.html").read_text(encoding="utf-8")
        bootstrap = (project / "app" / "__init__.py").read_text(encoding="utf-8")

        self.assertIn("vault.index", nav)
        self.assertIn("app.register_blueprint(vault_web.bp)", bootstrap)
        self.assertIn("Credentials durchsuchen", index)
        self.assertNotIn('name="password"', index.split("Credentials durchsuchen", 1)[1])
        self.assertIn("Passwort / Secret", detail)


if __name__ == "__main__":
    unittest.main()
