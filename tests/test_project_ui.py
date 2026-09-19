from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.project_store import ProjectStore


class ProjectUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
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
        documents = self.root / "documents"
        documents.mkdir(parents=True, exist_ok=True)
        app.config.update(
            TESTING=True,
            PROPAGATE_EXCEPTIONS=False,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(self.root / "project-ui.sqlite"),
            DOCUMENT_ROOT=str(documents),
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES (?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("project-ui-admin", generate_password_hash("project-ui-password")),
            )
            db.commit()
        self.client = app.test_client()
        response = self.client.post(
            "/auth/login",
            data={"username": "project-ui-admin", "password": "project-ui-password"},
        )
        self.assertLess(response.status_code, 400)

        store = ProjectStore(documents)
        self.project = store.create_project(
            {
                "title": "VK Lt",
                "location": "Duisburg",
                "planned_start": "2026-09-01",
                "planned_end": "2026-10-31",
                "resources": "Team A",
                "description": "UI Testprojekt",
            },
            "project-ui-admin",
        )
        store.add_task(
            self.project["project_id"],
            {
                "title": "Anforderungen aufnehmen",
                "status": "in_progress",
                "planned_end": "2026-09-30",
                "resources": "Max",
            },
            "project-ui-admin",
        )
        store.add_task(
            self.project["project_id"],
            {
                "title": "Review",
                "status": "completed",
                "planned_end": "2026-10-10",
                "resources": "Anna",
            },
            "project-ui-admin",
        )

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_project_overview_renders_new_mobile_cards(self):
        response = self.client.get("/documents/projects")
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn('data-project-overview', body)
        self.assertIn("VK Lt", body)
        self.assertIn("Projekt anlegen", body)
        self.assertIn("project-progress-track", body)
        self.assertIn("/static/css/project_ui.css", body)
        self.assertIn("/static/js/project_ui.js", body)

    def test_project_detail_renders_dashboard_and_task_preview(self):
        project_id = self.project["project_id"]
        response = self.client.get(f"/documents/projects/{project_id}")
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn('data-project-ui="v2"', body)
        self.assertIn("Projektfortschritt", body)
        self.assertIn("Projektinfos", body)
        self.assertIn("Anforderungen aufnehmen", body)
        self.assertIn(f"/documents/projects/{project_id}/tasks", body)
        self.assertNotIn("Projektcockpit", body)

    def test_project_tasks_workspace_and_return_redirect(self):
        project_id = self.project["project_id"]
        response = self.client.get(f"/documents/projects/{project_id}/tasks")
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("Neue Aufgabe anlegen", body)
        self.assertIn('data-project-tasks', body)
        self.assertIn('data-task-status-filter="in_progress"', body)
        self.assertIn("Anforderungen aufnehmen", body)

        response = self.client.post(
            f"/documents/projects/{project_id}/tasks",
            data={
                "title": "Neue UI Aufgabe",
                "status": "open",
                "return_to": "project_tasks",
            },
        )
        self.assertEqual(302, response.status_code)
        self.assertIn(f"/documents/projects/{project_id}/tasks", response.headers["Location"])


if __name__ == "__main__":
    unittest.main()
