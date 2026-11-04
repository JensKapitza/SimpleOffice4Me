"""Admin allowlists from the disk store + admin UI (no environment)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app import app, app_config_store, db as database
from app import admin_lists as lists
from app.virtual_filesystem import VirtualFileSystem


class AdminListsStoreTests(unittest.TestCase):
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
        self.flask.config.update(SECRET_KEY="admin-lists-test")
        self.context = self.flask.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)

    def test_unset_lists_are_empty_fail_closed(self):
        self.assertEqual(set(), lists.security_admins())
        self.assertEqual(set(), lists.document_admins())
        self.assertEqual(
            {"security_admins": "", "document_admins": ""}, lists.status(),
        )

    def test_save_normalizes_and_roundtrips(self):
        status = lists.save({
            "security_admins": " owner , owner, worker ",
            "document_admins": "docs-user",
        })
        self.assertEqual("owner,worker", status["security_admins"])
        self.assertEqual({"owner", "worker"}, lists.security_admins())
        self.assertEqual({"docs-user"}, lists.document_admins())

    def test_empty_save_clears_and_partial_save_keeps_other(self):
        lists.save({"security_admins": "owner", "document_admins": "docs-user"})
        lists.save({"security_admins": ""})
        self.assertEqual(set(), lists.security_admins())
        self.assertEqual({"docs-user"}, lists.document_admins())

    def test_invalid_values_fail_bilingual(self):
        with self.assertRaises(ValueError) as german:
            lists.save({"security_admins": "x" * 4097})
        self.assertIn("Security-Admin", str(german.exception))
        with self.assertRaises(ValueError) as english:
            lists.save({"document_admins": ["nope"]}, "en")
        self.assertIn("document admin list", str(english.exception).lower())

    def test_filesystem_uses_store_names(self):
        self.assertFalse(hasattr(VirtualFileSystem, "from_environment"))
        lists.save({"document_admins": "docs-user"})
        vfs = VirtualFileSystem.from_store(Path(self.temp.name))
        self.assertIn("docs-user", vfs.administrators)

    def test_security_admin_check_uses_store(self):
        from app.documents_core import _security_admin

        self.assertFalse(_security_admin("owner"))
        lists.save({"security_admins": "owner"})
        self.assertTrue(_security_admin("owner"))
        self.assertFalse(_security_admin("stranger"))


class AdminListsAdminTests(unittest.TestCase):
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
            data={"username": "admin", "password": "synthetic-lists-password"},
        )
        self.client.post(
            "/auth/login",
            data={"username": "admin", "password": "synthetic-lists-password"},
        )

    def test_admin_can_save_lists_and_sees_values(self):
        response = self.client.post(
            "/admin/users/admin-lists",
            data={"security_admins": "owner", "document_admins": "docs-user, docs-admin"},
        )
        self.assertEqual(302, response.status_code)
        with app.app_context():
            self.assertEqual({"owner"}, lists.security_admins())
            self.assertEqual({"docs-admin", "docs-user"}, lists.document_admins())
        page = self.client.get("/admin/users")
        self.assertEqual(200, page.status_code)
        body = page.get_data(as_text=True)
        self.assertIn('id="admin-lists"', body)
        self.assertIn("owner", body)

    def test_non_admin_cannot_save_lists(self):
        with app.app_context():
            conn = database.get_db()
            conn.execute("UPDATE user SET is_admin=0 WHERE username='admin'")
            conn.commit()
        response = self.client.post(
            "/admin/users/admin-lists", data={"security_admins": "owner"},
        )
        self.assertEqual(403, response.status_code)


if __name__ == "__main__":
    unittest.main()
