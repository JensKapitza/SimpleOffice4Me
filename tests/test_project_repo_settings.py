"""Project repository roots from the settings file + admin UI (no environment)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app, db as database
from app.document_store import CONTROL_DIR
from app.project_git import (
    ProjectGitService,
    normalize_repo_roots,
    validate_repo_roots,
)
from app.settings_store import SettingsStore


def _absolute_child(base: Path, name: str = "repos") -> str:
    child = base / name
    child.mkdir(parents=True, exist_ok=True)
    return str(child)


class ProjectRepoStoreTests(unittest.TestCase):
    def test_defaults_allow_only_the_document_root(self):
        with tempfile.TemporaryDirectory() as temp:
            section = SettingsStore(Path(temp)).settings()["projects"]
            self.assertEqual({"repo_roots": []}, section)
            service = ProjectGitService(Path(temp), {})
            self.assertEqual([service.root], service._allowed_roots())

    def test_save_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            allowed = _absolute_child(root)
            store = SettingsStore(root)
            store.save({"projects": {"repo_roots": [allowed]}}, "repo-test")
            self.assertEqual([allowed], store.settings()["projects"]["repo_roots"])
            with self.assertRaises(ValueError):
                validate_repo_roots("not-a-list")
            with self.assertRaises(ValueError):
                validate_repo_roots(["relative/path"])
            with self.assertRaises(ValueError):
                validate_repo_roots([42])
            with self.assertRaises(ValueError):
                validate_repo_roots(["x" * 1025])
            with self.assertRaises(ValueError):
                validate_repo_roots([allowed] * 65)
            self.assertEqual(
                [allowed],
                normalize_repo_roots([allowed, "relative", allowed, 42, ""]),
            )

    def test_invalid_stored_values_are_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / CONTROL_DIR / "settings.json"
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(
                json.dumps(
                    {"projects": {"repo_roots": ["relative/path", 42, ""]}},
                ),
                encoding="utf-8",
            )
            self.assertEqual([], SettingsStore(root).settings()["projects"]["repo_roots"])

    def test_service_honors_store_and_ignores_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            allowed = _absolute_child(root, "allowed")
            decoy = _absolute_child(root, "decoy")
            missing = str(root / "missing")
            SettingsStore(root).save(
                {"projects": {"repo_roots": [allowed, missing]}}, "repo-test",
            )
            service = ProjectGitService(root, {})
            with patch.dict(
                os.environ,
                {"SIMPLEOFFICE_PROJECT_REPO_ROOTS": decoy},
                clear=False,
            ):
                roots = service._allowed_roots()
            self.assertIn(service.root, roots)
            self.assertIn(Path(allowed).resolve(), roots)
            self.assertNotIn(Path(decoy).resolve(), roots)
            self.assertNotIn(Path(missing), roots)


class ProjectRepoAdminTests(unittest.TestCase):
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
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post(
            "/auth/register",
            data={"username": "admin", "password": "synthetic-repo-password"},
        )
        self.client.post(
            "/auth/login",
            data={"username": "admin", "password": "synthetic-repo-password"},
        )

    def _settings(self):
        return SettingsStore(self.root).settings()["projects"]["repo_roots"]

    def test_admin_can_save_roots(self):
        allowed = _absolute_child(self.base, "srv-git")
        response = self.client.post(
            "/admin/projects/repo-roots",
            data={"repo_roots": allowed + "\n\n" + allowed + "\n"},
        )
        self.assertEqual(302, response.status_code)
        self.assertEqual([allowed], self._settings())
        page = self.client.get("/admin/projects/repo-roots")
        self.assertEqual(200, page.status_code)
        body = page.get_data(as_text=True)
        self.assertIn("repo-roots", body)
        self.assertIn("Project repository roots", body)

    def test_admin_save_rejects_relative_paths(self):
        allowed = _absolute_child(self.base, "srv-git")
        self.client.post(
            "/admin/projects/repo-roots", data={"repo_roots": allowed},
        )
        rejected = self.client.post(
            "/admin/projects/repo-roots", data={"repo_roots": "relative/path"},
        )
        self.assertEqual(302, rejected.status_code)
        self.assertEqual([allowed], self._settings())

    def test_admin_can_reset_roots(self):
        self.client.post(
            "/admin/projects/repo-roots",
            data={"repo_roots": _absolute_child(self.base, "srv-git")},
        )
        reset = self.client.post(
            "/admin/projects/repo-roots", data={"reset": "1"},
        )
        self.assertEqual(302, reset.status_code)
        self.assertEqual([], self._settings())

    def test_admin_save_is_audited(self):
        self.client.post(
            "/admin/projects/repo-roots",
            data={"repo_roots": _absolute_child(self.base, "srv-git")},
        )
        with app.app_context():
            row = database.get_db().execute(
                "SELECT actor_name,target_id FROM security_event "
                "WHERE action=? ORDER BY rowid DESC LIMIT 1",
                ("project_repo_roots_updated",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual("projects.repo_roots", row["target_id"])

    def test_non_admin_cannot_save_roots(self):
        with app.app_context():
            conn = database.get_db()
            conn.execute("UPDATE user SET is_admin=0 WHERE username='admin'")
            conn.commit()
        response = self.client.post(
            "/admin/projects/repo-roots",
            data={"repo_roots": _absolute_child(self.base, "srv-git")},
        )
        self.assertEqual(403, response.status_code)
        self.assertEqual([], self._settings())


if __name__ == "__main__":
    unittest.main()
