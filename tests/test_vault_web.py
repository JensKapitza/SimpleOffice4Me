import io
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from flask import Flask, g

from app.password_vault import PasswordVault
from app.vault_web import _UNLOCKS, bp


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

        template = (Path(__file__).parents[1] / "templates" / "vault" / "index.html").read_text(
            encoding="utf-8"
        )
        search_section = template.split("Credentials durchsuchen", 1)[1]
        self.assertNotIn("row.password", search_section)
        self.assertNotIn("row.totp", search_section)
        self.assertNotIn("row.notes", search_section)

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

    def test_reunlock_replaces_previous_process_unlock_token(self):
        self._setup()
        with self.client.session_transaction() as current:
            first = current["vault_unlock_token"]

        response = self.client.post(
            "/vault/unlock",
            data={"master_password": self.password},
        )
        self.assertEqual(302, response.status_code)
        with self.client.session_transaction() as current:
            second = current["vault_unlock_token"]

        self.assertNotEqual(first, second)
        self.assertNotIn(first, _UNLOCKS)
        self.assertIn(second, _UNLOCKS)

    def test_master_password_change_invalidates_other_actor_unlock_tokens(self):
        self._setup()
        other = self.app.test_client()
        response = other.post(
            "/vault/unlock",
            data={"master_password": self.password},
        )
        self.assertEqual(302, response.status_code)
        with other.session_transaction() as other_session:
            other_token = other_session["vault_unlock_token"]
        self.assertIn(other_token, _UNLOCKS)

        replacement = "new correct horse battery staple"
        response = self.client.post(
            "/vault/master-password",
            data={
                "old_password": self.password,
                "new_password": replacement,
                "new_password_confirm": replacement,
            },
        )
        self.assertEqual(302, response.status_code)
        with self.client.session_transaction() as current:
            replacement_token = current["vault_unlock_token"]

        self.assertNotIn(other_token, _UNLOCKS)
        self.assertIn(replacement_token, _UNLOCKS)

    def test_browser_csv_import_never_overwrites_existing_match(self):
        self._setup()
        self._add(password="original-secret")
        raw = (
            "url,username,password\n"
            "https://example.org/login,alice@example.org,replacement-secret\n"
            "https://other.example/,bob,new-secret\n"
            "https://other.example/,bob,duplicate-in-file\n"
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

    def test_storage_failure_while_listing_is_handled_without_http_500(self):
        self._setup()
        with patch.object(PasswordVault, "entries", side_effect=OSError("storage unavailable")):
            response = self.client.get("/vault/")
        self.assertEqual(200, response.status_code)
        self.assertIn(b"Vault-Daten konnten nicht gelesen werden.", response.data)

    def test_storage_failure_while_saving_is_handled_without_http_500(self):
        self._setup()
        with patch.object(PasswordVault, "put", side_effect=OSError("storage unavailable")):
            response = self.client.post(
                "/vault/entries",
                data={"type": "login", "name": "Storage failure"},
            )
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith("/vault/"))

    def test_storage_failure_while_exporting_backup_is_handled_without_http_500(self):
        self._setup()
        with patch.object(
            PasswordVault,
            "export_backup",
            side_effect=OSError("storage unavailable"),
        ):
            response = self.client.post("/vault/export/backup")
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith("/vault/"))

    def test_plaintext_export_requires_explicit_confirmation_and_no_store(self):
        self._setup()
        self._add()
        denied = self.client.post("/vault/export/browser-csv", data={"confirm": ""})
        self.assertEqual(302, denied.status_code)

        wrong = self.client.post(
            "/vault/export/browser-csv",
            data={"confirm": "EXPORT", "master_password": "wrong password value"},
        )
        self.assertEqual(302, wrong.status_code)

        response = self.client.post(
            "/vault/export/browser-csv",
            data={"confirm": "EXPORT", "master_password": self.password},
        )
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
