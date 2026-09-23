from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.project_git import ProjectGitError
from app.project_store import ProjectStore


class ProjectTrackerUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.documents = self.root / "documents"
        self.documents.mkdir()
        self.saved = {
            key: app.config.get(key)
            for key in (
                "DATABASE",
                "DOCUMENT_ROOT",
                "TESTING",
                "PROPAGATE_EXCEPTIONS",
                "TEST_CSRF_PROTECTION",
            )
        }
        app.config.update(
            TESTING=True,
            PROPAGATE_EXCEPTIONS=False,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(self.root / "tracker-ui.sqlite"),
            DOCUMENT_ROOT=str(self.documents),
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES (?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("tracker-admin", generate_password_hash("tracker-password")),
            )
            db.commit()
        self.client = app.test_client()
        response = self.client.post(
            "/auth/login",
            data={"username": "tracker-admin", "password": "tracker-password"},
        )
        self.assertLess(response.status_code, 400)
        self.store = ProjectStore(self.documents)
        self.project = self.store.create_project({"title": "Mini Tracker"}, "tracker-admin")

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_project_tabs_issues_and_safe_wiki_rendering(self):
        project_id = self.project["project_id"]
        overview = self.client.get(f"/documents/projects/{project_id}")
        self.assertEqual(200, overview.status_code)
        body = overview.get_data(as_text=True)
        self.assertIn(">Issues<", body)
        self.assertIn(">Wiki<", body)
        self.assertIn(">Aktivität<", body)
        self.assertNotIn(">Code<", body)

        created = self.client.post(
            f"/documents/projects/{project_id}/issues",
            data={
                "title": "Fehler im Import",
                "type": "bug",
                "priority": "high",
                "body_markdown": "**Reproduzierbar**",
            },
            follow_redirects=True,
        )
        self.assertEqual(200, created.status_code)
        issue_body = created.get_data(as_text=True)
        self.assertIn("#1 Fehler im Import", issue_body)
        self.assertIn("<strong>Reproduzierbar</strong>", issue_body)

        wiki = self.client.post(
            f"/documents/projects/{project_id}/wiki",
            data={
                "title": "Home",
                "slug": "home",
                "expected_revision": "0",
                "body_markdown": "# Dokumentation\n\n<script>alert(1)</script>",
            },
            follow_redirects=True,
        )
        self.assertEqual(200, wiki.status_code)
        wiki_body = wiki.get_data(as_text=True)
        self.assertIn("<h1>Dokumentation</h1>", wiki_body)
        self.assertIn("&lt;script&gt;", wiki_body)
        self.assertNotIn("<script>alert(1)</script>", wiki_body)

    def test_non_admin_cannot_change_repository_path(self):
        project_id = self.project["project_id"]
        self.store.update_project(
            project_id,
            {"title": "Mini Tracker", "repository_path": "source"},
            "tracker-admin",
        )
        with app.app_context():
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES (?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("tracker-worker", generate_password_hash("worker-password")),
            )
            db.commit()

        worker = app.test_client()
        login = worker.post(
            "/auth/login",
            data={"username": "tracker-worker", "password": "worker-password"},
        )
        self.assertLess(login.status_code, 400)

        page = worker.get(f"/documents/projects/{project_id}")
        self.assertEqual(200, page.status_code)
        self.assertNotIn("edit-project-repository", page.get_data(as_text=True))

        response = worker.post(
            f"/documents/projects/{project_id}",
            data={
                "title": "Mini Tracker",
                "status": "open",
                "repository_path": "other-repository",
            },
        )
        self.assertEqual(302, response.status_code)
        self.assertEqual(
            "source",
            self.store.project(project_id)["repository_path"],
        )

        created = worker.post(
            "/documents/projects",
            data={
                "title": "Worker project",
                "repository_path": "other-repository",
            },
        )
        self.assertEqual(302, created.status_code)
        worker_project = next(
            row for row in self.store.projects()
            if row["title"] == "Worker project"
        )
        self.assertEqual("", worker_project["repository_path"])

    @unittest.skipUnless(shutil.which("git"), "git is required")
    def test_issue_page_survives_git_history_failure(self):
        project_id = self.project["project_id"]
        repo = self.documents / "source"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        (repo / "README.md").write_text("# Repository\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "Initial"],
            cwd=repo, check=True, capture_output=True,
        )
        self.store.update_project(
            project_id,
            {"title": "Mini Tracker", "repository_path": "source"},
            "tracker-admin",
        )
        created = self.client.post(
            f"/documents/projects/{project_id}/issues",
            data={"title": "Git-unabhängiges Issue", "type": "bug", "priority": "normal"},
        )
        self.assertEqual(302, created.status_code)

        with patch(
            "app.documents_routes_project_tracker.ProjectGitService.commits_for_issue",
            side_effect=ProjectGitError("synthetic git failure"),
        ):
            response = self.client.get(f"/documents/projects/{project_id}/issues/1")

        self.assertEqual(200, response.status_code)
        self.assertIn("Git-unabhängiges Issue", response.get_data(as_text=True))

    @unittest.skipUnless(shutil.which("git"), "git is required")
    def test_code_tab_appears_only_after_real_git_repository_is_configured(self):
        project_id = self.project["project_id"]
        repo = self.documents / "source"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        (repo / "README.md").write_text("# Repository\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "Initial"],
            cwd=repo, check=True, capture_output=True,
        )
        self.store.update_project(
            project_id,
            {"title": "Mini Tracker", "repository_path": "source"},
            "tracker-admin",
        )

        overview = self.client.get(f"/documents/projects/{project_id}")
        self.assertIn(">Code<", overview.get_data(as_text=True))

        code = self.client.get(f"/documents/projects/{project_id}/code")
        self.assertEqual(200, code.status_code)
        code_body = code.get_data(as_text=True)
        self.assertIn("README.md", code_body)
        self.assertIn("Initial", code_body)

        file_response = self.client.get(
            f"/documents/projects/{project_id}/code/file",
            query_string={"path": "README.md"},
        )
        self.assertEqual(200, file_response.status_code)
        self.assertIn("<h1>Repository</h1>", file_response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
