from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from app import db as database
from app.todo_store import TodoStore


class V3AndroidOfflineRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.documents = root / "documents"
        self.documents.mkdir()
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.addCleanup(lambda: app.config.update(self.saved))
        app.config.update(
            TESTING=True,
            DATABASE=str(root / "users.sqlite"),
            DOCUMENT_ROOT=str(self.documents),
        )
        self.env = patch.dict(
            os.environ,
            {"SIMPLEOFFICE_V3_ANDROID_OFFLINE_ENABLED": "1"},
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user (username, password, is_admin) VALUES (?, ?, 1)",
                ("admin", "unused"),
            )
            db.execute(
                "INSERT INTO user (username, password, is_admin) VALUES (?, ?, 0)",
                ("limited", "unused"),
            )
            db.execute(
                "INSERT INTO user (username, password, is_admin) VALUES (?, ?, 0)",
                ("viewer", "unused"),
            )
            db.execute(
                "INSERT INTO user_permission(user_id, feature, enabled, updated_at) "
                "SELECT id, 'projects', 0, CURRENT_TIMESTAMP FROM user WHERE username='limited'"
            )
            db.commit()
            self.admin_id = db.execute(
                "SELECT id FROM user WHERE username='admin'"
            ).fetchone()["id"]
            self.limited_id = db.execute(
                "SELECT id FROM user WHERE username='limited'"
            ).fetchone()["id"]
            self.viewer_id = db.execute(
                "SELECT id FROM user WHERE username='viewer'"
            ).fetchone()["id"]

        self.store = TodoStore(self.documents)
        self.task = self.store.add(
            "Offline prüfen",
            "admin",
            {"status": "needs-action", "due": "2026-10-05"},
        )
        self.client = app.test_client()
        self._login(self.admin_id)

    def _login(self, user_id: int):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["_csrf_token"] = "x" * 40
        self.headers = {"X-CSRF-Token": "x" * 40}

    def test_capability_off_keeps_api_undiscoverable(self):
        with patch.dict(
            os.environ,
            {"SIMPLEOFFICE_V3_ANDROID_OFFLINE_ENABLED": "0"},
            clear=False,
        ):
            response = self.client.get("/api/v3/android-offline/tasks")
        self.assertEqual(404, response.status_code)

    def test_authenticated_owner_is_rendered_and_login_page_has_no_owner_binding(self):
        page = self.client.get("/android/offline")
        self.assertEqual(200, page.status_code)
        self.assertIn(
            f'data-v3-android-offline-owner="{self.admin_id}"'.encode(),
            page.data,
        )

        with self.client.session_transaction() as session:
            session.clear()
        login = self.client.get("/auth/login")
        self.assertEqual(200, login.status_code)
        self.assertIn(b'data-v3-android-offline-owner=""', login.data)

    def test_policy_classifies_sensitive_and_online_only_data(self):
        response = self.client.get("/api/v3/android-offline/policy")
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(["task"], payload["offline_allowed"])
        self.assertEqual(["task_status"], payload["mutation_types"])
        self.assertIn("mail", payload["online_only"])
        self.assertIn("federation_private_key", payload["never_local"])
        self.assertIn("oauth_token", payload["never_local"])

    def test_task_projection_has_strong_version_without_credentials(self):
        response = self.client.get("/api/v3/android-offline/tasks")
        self.assertEqual(200, response.status_code)
        task = response.get_json()["tasks"][0]
        self.assertEqual(self.task["id"], task["id"])
        self.assertEqual(self.store.etag(self.task), task["version"])
        self.assertNotIn("password", task)
        self.assertNotIn("token", task)

        detail = self.client.get(f"/api/v3/android-offline/tasks/{self.task['id']}")
        self.assertEqual(200, detail.status_code)
        self.assertEqual(task["version"], detail.headers["ETag"])
        self.assertEqual("no-store", detail.headers["Cache-Control"])

    def test_task_status_sync_is_idempotent(self):
        base = self.store.etag(self.task)
        operation = {
            "operationId": "android-sync-0001",
            "mutationType": "task_status",
            "targetId": self.task["id"],
            "baseVersion": base,
            "payload": {"status": "in-process"},
        }

        first = self.client.post(
            "/api/v3/android-offline/sync",
            json={"operations": [operation]},
            headers=self.headers,
        )
        replay = self.client.post(
            "/api/v3/android-offline/sync",
            json={"operations": [operation]},
            headers=self.headers,
        )

        self.assertEqual(200, first.status_code)
        self.assertEqual("synced", first.get_json()["results"][0]["status"])
        self.assertTrue(replay.get_json()["results"][0]["replayed"])
        self.assertEqual("in-process", self.store.items("admin")[0]["status"])

    def test_stale_base_version_is_reported_as_conflict(self):
        base = self.store.etag(self.task)
        self.store.update(self.task["id"], {"description": "Serveränderung"}, "admin")

        response = self.client.post(
            "/api/v3/android-offline/sync",
            json={
                "operations": [{
                    "operationId": "android-sync-0002",
                    "mutationType": "task_status",
                    "targetId": self.task["id"],
                    "baseVersion": base,
                    "payload": "{\"status\":\"completed\"}",
                }]
            },
            headers=self.headers,
        )

        result = response.get_json()["results"][0]
        self.assertEqual(200, response.status_code)
        self.assertEqual("conflict", result["status"])
        self.assertNotEqual(base, result["serverVersion"])
        self.assertNotEqual("completed", self.store.items("admin")[0]["status"])

    def test_unsupported_mutation_is_rejected_without_writing(self):
        base = self.store.etag(self.task)
        response = self.client.post(
            "/api/v3/android-offline/sync",
            json={
                "operations": [{
                    "operationId": "android-sync-0003",
                    "mutationType": "note",
                    "targetId": self.task["id"],
                    "baseVersion": base,
                    "payload": {"text": "nicht erlaubt"},
                }]
            },
            headers=self.headers,
        )
        self.assertEqual("rejected", response.get_json()["results"][0]["status"])
        self.assertEqual("needs-action", self.store.items("admin")[0]["status"])

    def test_read_only_shared_task_cannot_be_changed_offline(self):
        shared_list = self.store.create_list(
            {"name": "Offline lesbar"},
            "admin",
            "offline-read",
        )
        self.store.update_list(
            shared_list["list_id"],
            {"permissions": {"viewer": ["read"]}},
            "admin",
        )
        shared = self.store.add(
            "Nur lesen",
            "admin",
            {"list_id": shared_list["list_id"], "status": "needs-action"},
        )
        self._login(self.viewer_id)

        visible = self.client.get("/api/v3/android-offline/tasks")
        self.assertEqual(200, visible.status_code)
        self.assertIn(shared["id"], [row["id"] for row in visible.get_json()["tasks"]])

        response = self.client.post(
            "/api/v3/android-offline/sync",
            json={
                "operations": [{
                    "operationId": "android-readonly-0001",
                    "mutationType": "task_status",
                    "targetId": shared["id"],
                    "baseVersion": self.store.etag(shared),
                    "payload": {"status": "in-process"},
                }]
            },
            headers=self.headers,
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual("rejected", response.get_json()["results"][0]["status"])
        unchanged = next(row for row in self.store.items("admin") if row["id"] == shared["id"])
        self.assertEqual("needs-action", unchanged["status"])

    def test_projects_permission_is_required(self):
        self._login(self.limited_id)
        self.assertEqual(403, self.client.get("/api/v3/android-offline/policy").status_code)
        self.assertEqual(403, self.client.get("/android/offline").status_code)


if __name__ == "__main__":
    unittest.main()
