from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.project_git import ProjectGitError, ProjectGitService
from app.project_markdown import render_markdown
from app.project_store import ProjectStore
from app.project_tracker import ProjectTrackerStore


class ProjectTrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.projects = ProjectStore(self.root)
        self.project = self.projects.create_project({"title": "Tracker"}, "alice")
        self.store = ProjectTrackerStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_issue_numbers_comments_and_updates_are_persistent(self):
        first = self.store.create_issue(self.project["project_id"], {"title": "Bug", "type": "bug"}, "alice")
        second = self.store.create_issue(self.project["project_id"], {"title": "Feature", "type": "feature"}, "alice")
        self.store.add_comment(self.project["project_id"], first["number"], "**Hinweis**", "bob")
        updated = self.store.update_issue(
            self.project["project_id"],
            first["number"],
            {**first, "title": "Bug fix", "status": "in_progress", "labels": "backend, security"},
            "alice",
            expected_updated_at=first["updated_at"],
        )

        self.assertEqual(1, first["number"])
        self.assertEqual(2, second["number"])
        self.assertEqual("in_progress", updated["status"])
        self.assertEqual(["backend", "security"], updated["labels"])
        self.assertEqual("bob", self.store.issue(self.project["project_id"], 1)["comments"][0]["created_by"])

    def test_stale_issue_update_is_rejected(self):
        issue = self.store.create_issue(self.project["project_id"], {"title": "A"}, "alice")
        self.store.add_comment(self.project["project_id"], issue["number"], "changed", "bob")
        with self.assertRaisesRegex(ValueError, "changed since"):
            self.store.update_issue(
                self.project["project_id"],
                issue["number"],
                {**issue, "title": "stale"},
                "alice",
                expected_updated_at=issue["updated_at"],
            )

    def test_wiki_revision_and_activity(self):
        page = self.store.save_wiki_page(
            self.project["project_id"],
            {"title": "Home", "slug": "home", "body_markdown": "# Start"},
            "alice",
            expected_revision=0,
        )
        changed = self.store.save_wiki_page(
            self.project["project_id"],
            {"title": "Home", "slug": "home", "body_markdown": "# Neu"},
            "bob",
            expected_revision=1,
        )
        self.assertEqual(2, changed["revision"])
        self.assertEqual("# Neu", self.store.wiki_page(self.project["project_id"], "home")["body_markdown"])
        self.assertEqual("wiki_updated", self.store.activity(self.project["project_id"])[0]["kind"])
        with self.assertRaisesRegex(ValueError, "changed since"):
            self.store.save_wiki_page(
                self.project["project_id"],
                {"title": "Home", "slug": "home", "body_markdown": "stale"},
                "alice",
                expected_revision=page["revision"],
            )

    def test_corrupt_tracker_data_fails_closed(self):
        self.store.initialize()
        self.store.path.write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "corrupt"):
            self.store.issues(self.project["project_id"])

    def test_markdown_escapes_html_and_rejects_javascript_links(self):
        rendered = str(render_markdown(
            "# Titel\n\n<script>alert(1)</script>\n\n[x](javascript:alert(2))\n\n- [x] fertig\n\n| A | B |\n|---|---|\n| 1 | 2 |"
        ))
        self.assertIn("<h1>Titel</h1>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("javascript:", rendered)
        self.assertIn('type="checkbox" checked disabled', rendered)
        self.assertIn("<table", rendered)

    @unittest.skipUnless(shutil.which("git"), "git is required")
    def test_git_views_exist_only_for_real_allowed_repository(self):
        repo = self.root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=Alice", "-c", "user.email=alice@example.invalid", "commit", "-m", "Refs #1"],
            cwd=repo, check=True, capture_output=True,
        )
        project = {**self.project, "repository_path": "repo"}
        service = ProjectGitService(self.root, project)

        self.assertTrue(service.available())
        self.assertEqual("README.md", service.tree()[0]["name"])
        self.assertEqual("# Demo\n", service.text_file("README.md")["text"])
        self.assertEqual("Refs #1", service.commits_for_issue(1)[0]["subject"])

        outside = Path(self.temp.name).parent
        with patch.dict(os.environ, {"SIMPLEOFFICE_PROJECT_REPO_ROOTS": ""}, clear=False):
            denied = ProjectGitService(self.root, {**self.project, "repository_path": str(outside)})
            self.assertFalse(denied.available())
            with self.assertRaises(ProjectGitError):
                denied.repository_path()

    def test_project_repository_path_is_preserved_on_other_edits(self):
        created = self.projects.create_project({"title": "Repo", "repository_path": "source/repo"}, "alice")
        changed = self.projects.update_project(created["project_id"], {"title": "Repo 2"}, "alice")
        self.assertEqual("source/repo", changed["repository_path"])


if __name__ == "__main__":
    unittest.main()
