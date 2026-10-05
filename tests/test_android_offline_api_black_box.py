"""HTTP contracts from docs/ANDROID.md and docs/ANDROID_INTEGRATION.md.

Only deployment/bootstrap config is set directly. Users, sessions and tasks are
created through HTTP; assertions never read a store, database or session. ETags
and IDs are opaque values received from the API, not calculated by test code.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from app import app
from app.db import ensure_auth_database


class CsrfMetadata(HTMLParser):
    """Read the same public HTML metadata that a browser client receives."""

    def __init__(self):
        super().__init__()
        self.token = ""

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta" and attributes.get("name") == "csrf-token":
            self.token = attributes.get("content", "")


class AndroidOfflineApiBlackBoxTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        previous = app.config.copy()
        self.addCleanup(self._restore_config, previous)
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            DATABASE=str(root / "auth.sqlite3"),
            DOCUMENT_ROOT=str(root / "documents"),
        )
        environment = patch.dict(os.environ, {"SIMPLEOFFICE_V3_ANDROID_OFFLINE_ENABLED": "1"})
        environment.start()
        self.addCleanup(environment.stop)
        # The normal application-start schema initializer: no fixture INSERTs,
        # domain methods, test password hashing or asserted persistence layout.
        with app.app_context():
            ensure_auth_database()

        self.client = app.test_client()
        self._register(self.client, "owner")
        self._login(self.client, "owner")
        response = self.client.post(
            "/tasks/",
            data={
                "title": "Überseehafen prüfen",
                "description": "Nur den Aufgabenstatus offline verändern.",
                "status": "needs-action",
                "due": "2030-02-28",
            },
            headers=self._csrf_headers(self.client),
        )
        self.assertEqual(302, response.status_code)
        response = self.client.get("/api/v3/android-offline/tasks")
        self.assertEqual(200, response.status_code)
        tasks = response.get_json()["tasks"]
        self.assertEqual(1, len(tasks))
        self.task_id = tasks[0]["id"]
        self.base_version = tasks[0]["version"]

    @staticmethod
    def _restore_config(previous):
        app.config.clear()
        app.config.update(previous)

    def _csrf_headers(self, client, path="/tasks/"):
        page = client.get(path)
        self.assertEqual(200, page.status_code)
        parser = CsrfMetadata()
        parser.feed(page.get_data(as_text=True))
        self.assertTrue(parser.token, "Public HTML must supply the browser CSRF token")
        return {"X-CSRF-Token": parser.token}

    def _register(self, client, username):
        response = client.post(
            "/auth/register",
            data={"username": username, "password": "test-only-api-password"},
            headers=self._csrf_headers(client, "/auth/register"),
        )
        self.assertEqual(302, response.status_code)

    def _login(self, client, username):
        response = client.post(
            "/auth/login",
            data={"username": username, "password": "test-only-api-password"},
            headers=self._csrf_headers(client, "/auth/login"),
        )
        self.assertEqual(302, response.status_code)

    def _operation(self, operation_id, status, *, base_version=None):
        return {
            "operationId": operation_id,
            "mutationType": "task_status",
            "targetId": self.task_id,
            "baseVersion": self.base_version if base_version is None else base_version,
            "payload": {"status": status},
        }

    def _sync(self, operation, *, client=None):
        client = self.client if client is None else client
        response = client.post(
            "/api/v3/android-offline/sync",
            json={"operations": [operation]},
            headers=self._csrf_headers(client),
        )
        self.assertEqual(200, response.status_code)
        results = response.get_json()["results"]
        self.assertEqual(1, len(results))
        return results[0]

    def _read(self):
        response = self.client.get(f"/api/v3/android-offline/tasks/{self.task_id}")
        self.assertEqual(200, response.status_code)
        return response

    def test_publicly_created_task_preserves_fields_and_has_a_strong_etag(self):
        """GET the HTTP-created task: preserve inputs and expose a strong version."""
        response = self._read()
        task = response.get_json()
        self.assertEqual("Überseehafen prüfen", task["title"])
        self.assertEqual("Nur den Aufgabenstatus offline verändern.", task["description"])
        self.assertEqual("2030-02-28", task["due"])
        self.assertEqual("needs-action", task["status"])
        self.assertRegex(response.headers["ETag"], r'^"[^"\r\n]+"$')
        self.assertEqual(self.base_version, response.headers["ETag"])
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertNotIn("test-only-api-password", response.get_data(as_text=True))
        self.assertFalse(
            {"password", "token", "session_secret", "oauth_token", "api_key", "vault_secret"}
            & task.keys()
        )

    def test_sync_completion_changes_only_the_authorized_business_fields(self):
        """POST completed with the current ETag: persist status, preserve content."""
        result = self._sync(self._operation("blackbox-complete-001", "completed"))
        self.assertEqual("synced", result["status"])
        response = self._read()
        task = response.get_json()
        self.assertEqual("completed", task["status"])
        self.assertEqual("Überseehafen prüfen", task["title"])
        self.assertEqual("Nur den Aufgabenstatus offline verändern.", task["description"])
        self.assertEqual("2030-02-28", task["due"])
        self.assertNotEqual(self.base_version, response.headers["ETag"])

    def test_replay_from_a_new_session_cannot_undo_a_later_successful_operation(self):
        """Retry the original operation after cancellation: remain cancelled."""
        original = self._operation("blackbox-replay-001", "in-process")
        self.assertEqual("synced", self._sync(original)["status"])
        current_version = self._read().headers["ETag"]
        later = self._operation("blackbox-replay-002", "cancelled", base_version=current_version)
        self.assertEqual("synced", self._sync(later)["status"])
        before_replay = self._read().headers["ETag"]

        new_session = app.test_client()
        self._login(new_session, "owner")
        replay = self._sync(original, client=new_session)
        self.assertEqual("synced", replay["status"])
        self.assertTrue(replay["replayed"])
        after_replay = self._read()
        self.assertEqual("cancelled", after_replay.get_json()["status"])
        self.assertEqual(before_replay, after_replay.headers["ETag"])

    def test_stale_version_reports_conflict_and_preserves_the_newer_status(self):
        """POST cancelled with a stale ETag: conflict, never last-write-wins."""
        self.assertEqual(
            "synced", self._sync(self._operation("blackbox-conflict-001", "completed"))["status"]
        )
        server_version = self._read().headers["ETag"]
        result = self._sync(self._operation("blackbox-conflict-002", "cancelled"))
        self.assertEqual("conflict", result["status"])
        unchanged = self._read()
        self.assertEqual("completed", unchanged.get_json()["status"])
        self.assertEqual(server_version, unchanged.headers["ETag"])

    def test_unsupported_mutations_are_rejected_without_changing_the_task(self):
        """Only task_status is writable; extra fields and unknown statuses fail."""
        operations = [
            {**self._operation("blackbox-reject-001", "completed"), "mutationType": "note"},
            {**self._operation("blackbox-reject-002", "completed"),
             "payload": {"status": "completed", "description": "Unzulässige Änderung"}},
            self._operation("blackbox-reject-003", "not-a-task-status"),
        ]
        for operation in operations:
            with self.subTest(operation=operation["operationId"]):
                self.assertEqual("rejected", self._sync(operation)["status"])
                response = self._read()
                self.assertEqual("needs-action", response.get_json()["status"])
                self.assertEqual("Nur den Aufgabenstatus offline verändern.",
                                 response.get_json()["description"])
                self.assertEqual(self.base_version, response.headers["ETag"])

    def test_another_user_cannot_list_or_mutate_the_private_task(self):
        """A second login sees no private task and cannot complete its known ID."""
        self._register(self.client, "outsider")
        outsider = app.test_client()
        self._login(outsider, "outsider")
        listing = outsider.get("/api/v3/android-offline/tasks")
        self.assertEqual(200, listing.status_code)
        self.assertEqual([], listing.get_json()["tasks"])
        operation = self._operation("blackbox-denied-001", "completed")
        self.assertEqual("rejected", self._sync(operation, client=outsider)["status"])
        unchanged = self._read()
        self.assertEqual("needs-action", unchanged.get_json()["status"])
        self.assertEqual(self.base_version, unchanged.headers["ETag"])

    def test_cookie_authenticated_sync_without_csrf_is_denied_without_side_effects(self):
        """POST valid JSON without CSRF: 403, original task remains unchanged."""
        response = self.client.post(
            "/api/v3/android-offline/sync",
            json={"operations": [self._operation("blackbox-csrf-001", "completed")]},
        )
        self.assertEqual(403, response.status_code)
        unchanged = self._read()
        self.assertEqual("needs-action", unchanged.get_json()["status"])
        self.assertEqual(self.base_version, unchanged.headers["ETag"])

    def test_operation_ids_are_isolated_between_authenticated_users(self):
        """The same operation ID completes owner's task and cancels outsider's."""
        operation_id = "blackbox-actor-scoped-001"
        self.assertEqual(
            "synced", self._sync(self._operation(operation_id, "completed"))["status"]
        )
        self._register(self.client, "outsider")
        outsider = app.test_client()
        self._login(outsider, "outsider")
        created = outsider.post(
            "/tasks/", data={"title": "Zweite private Aufgabe", "status": "needs-action"},
            headers=self._csrf_headers(outsider),
        )
        self.assertEqual(302, created.status_code)
        listing = outsider.get("/api/v3/android-offline/tasks")
        self.assertEqual(200, listing.status_code)
        self.assertEqual(1, len(listing.get_json()["tasks"]))
        other_task = listing.get_json()["tasks"][0]
        result = self._sync({
            "operationId": operation_id,
            "mutationType": "task_status",
            "targetId": other_task["id"],
            "baseVersion": other_task["version"],
            "payload": {"status": "cancelled"},
        }, client=outsider)
        self.assertEqual("synced", result["status"])
        self.assertEqual(other_task["id"], result["targetId"])
        other_read = outsider.get(f"/api/v3/android-offline/tasks/{other_task['id']}")
        self.assertEqual(200, other_read.status_code)
        self.assertEqual("cancelled", other_read.get_json()["status"])
        self.assertEqual("completed", self._read().get_json()["status"])
