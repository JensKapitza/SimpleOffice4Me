from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from app import app
from app.db import ensure_auth_database
from app.todo_store import TodoStore
from app.v3_automation import AutomationStore


class _RunTokenParser(HTMLParser):
    def __init__(self, target_action):
        super().__init__()
        self.target_action = target_action
        self.current_action = ""
        self.token = ""

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self.current_action = attributes.get("action", "")
        elif tag == "input" and self.current_action == self.target_action and attributes.get("name") == "run_token":
            self.token = attributes.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.current_action = ""


class V3AutomationRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.previous = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION", "SECRET_KEY")
        }
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(Path(self.temp.name) / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
            SECRET_KEY="automation-route-test-secret",
        )
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "alice", "password": "browser-passwort"})
        self.client.post("/auth/login", data={"username": "alice", "password": "browser-passwort"})
        self.enabled = patch("app.v3_automation_routes.enabled", return_value=True)
        self.enabled.start()

    def tearDown(self):
        self.enabled.stop()
        app.config.update(self.previous)
        self.temp.cleanup()

    def _csrf(self):
        with self.client.session_transaction() as session:
            return session.get("_csrf_token", "")

    def _rule(self, **overrides):
        values = {
            "name": "Manual check",
            "enabled": True,
            "trigger": {"type": "check", "name": "manual.invoice.check"},
            "conditions": [{"field": "event.approved", "op": "eq", "value": True}],
            "actions": [{"type": "task.create", "payload": {"title": "Review invoice"}}],
        }
        values.update(overrides)
        return AutomationStore(self.root).create("alice", values)

    def _preview(self, rule):
        return self.client.post(
            f"/automation-v3/{rule.rule_id}/prepare-check-run",
            data={
                "_csrf_token": self._csrf(),
                "event_json": '{"id":"private-event-id","name":"manual.invoice.check","approved":true}',
            },
        )

    def _token(self, response, rule):
        parser = _RunTokenParser(f"/automation-v3/{rule.rule_id}/confirm-check-run")
        parser.feed(response.get_data(as_text=True))
        self.assertTrue(parser.token)
        return parser.token

    def test_preview_is_read_only_and_confirm_reuses_server_bound_context(self):
        rule = self._rule()
        self.assertEqual([], TodoStore(self.root).items("alice"))
        preview = self._preview(rule)
        self.assertEqual(200, preview.status_code)
        cache_control = preview.headers.get("Cache-Control", "").split(", ")
        self.assertIn("no-store", cache_control)
        self.assertIn("private", cache_control)
        self.assertEqual([], TodoStore(self.root).items("alice"))
        token = self._token(preview, rule)

        app.config["TEST_CSRF_PROTECTION"] = True
        self.assertEqual(403, self.client.post(
            f"/automation-v3/{rule.rule_id}/confirm-check-run",
            data={"run_token": token},
        ).status_code)
        confirm = {
            "_csrf_token": self._csrf(),
            "run_token": token,
            "confirm_run": "1",
            "event_json": '{"name":"manual.invoice.check","approved":false}',
        }
        response = self.client.post(f"/automation-v3/{rule.rule_id}/confirm-check-run", data=confirm)
        self.assertEqual(302, response.status_code)
        self.assertEqual(["Review invoice"], [row["title"] for row in TodoStore(self.root).items("alice")])
        self.client.post(f"/automation-v3/{rule.rule_id}/confirm-check-run", data=confirm)
        self.assertEqual(1, len(TodoStore(self.root).items("alice")))
        self.assertNotIn("private-event-id", repr(AutomationStore(self.root).logs("alice")))

    def test_new_preview_is_a_distinct_intentional_run(self):
        rule = self._rule()
        first = self._preview(rule)
        token_one = self._token(first, rule)
        second = self._preview(rule)
        token_two = self._token(second, rule)
        self.assertNotEqual(token_one, token_two)
        for token in (token_one, token_two):
            self.client.post(
                f"/automation-v3/{rule.rule_id}/confirm-check-run",
                data={"_csrf_token": self._csrf(), "run_token": token, "confirm_run": "1"},
            )
        self.assertEqual(2, len(TodoStore(self.root).items("alice")))

    def test_concurrent_confirmation_retries_create_one_task(self):
        rule = self._rule()
        preview = self._preview(rule)
        token = self._token(preview, rule)
        client_two = app.test_client()
        client_two.set_cookie("session", self.client.get_cookie("session").value)
        csrf = self._csrf()

        def confirm(client):
            return client.post(
                f"/automation-v3/{rule.rule_id}/confirm-check-run",
                data={"_csrf_token": csrf, "run_token": token, "confirm_run": "1"},
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(confirm, (self.client, client_two)))
        self.assertTrue(all(response.status_code == 302 for response in responses))
        self.assertEqual(1, len(TodoStore(self.root).items("alice")))

    def test_stale_version_inactive_and_non_check_rules_are_rejected(self):
        rule = self._rule()
        preview = self._preview(rule)
        token = self._token(preview, rule)
        AutomationStore(self.root).update(rule.rule_id, "alice", {"enabled": False})
        response = self.client.post(
            f"/automation-v3/{rule.rule_id}/confirm-check-run",
            data={"_csrf_token": self._csrf(), "run_token": token, "confirm_run": "1"},
        )
        self.assertEqual(302, response.status_code)
        self.assertEqual([], TodoStore(self.root).items("alice"))
        inactive = AutomationStore(self.root).get(rule.rule_id, "alice")
        self.assertEqual(409, self._preview(inactive).status_code)
        ordinary = self._rule(trigger={"type": "event", "name": "manual.invoice.check"})
        self.assertEqual(409, self._preview(ordinary).status_code)

    def test_foreign_owner_and_unsupported_action_cannot_be_previewed(self):
        foreign = AutomationStore(self.root).create("bob", {
            "name": "Bob check", "enabled": True,
            "trigger": {"type": "check", "name": "manual.invoice.check"},
            "conditions": [],
            "actions": [{"type": "task.create", "payload": {"title": "No"}}],
        })
        self.assertEqual(404, self.client.post(
            f"/automation-v3/{foreign.rule_id}/prepare-check-run",
            data={"_csrf_token": self._csrf(), "event_json": "{}"},
        ).status_code)
        unsupported = self._rule(actions=[{"type": "task.create", "payload": {"title": "one"}}, {"type": "task.create", "payload": {"title": "two"}}])
        self.assertEqual(409, self._preview(unsupported).status_code)
        unsafe_fields = self._rule(actions=[{
            "type": "task.create",
            "payload": {"title": "Review", "values": {"source_document_id": "private-doc"}},
        }])
        self.assertEqual(409, self._preview(unsafe_fields).status_code)

    def test_capability_and_csrf_are_enforced(self):
        rule = self._rule()
        preview = self._preview(rule)
        token = self._token(preview, rule)
        with patch("app.v3_automation_routes.has_feature", return_value=False):
            page = self.client.get("/automation-v3")
            self.assertNotIn("Preview task", page.get_data(as_text=True))
            self.assertEqual(403, self._preview(rule).status_code)
            self.assertEqual(403, self.client.post(
                f"/automation-v3/{rule.rule_id}/confirm-check-run",
                data={"_csrf_token": self._csrf(), "run_token": token, "confirm_run": "1"},
            ).status_code)
        self.assertEqual([], TodoStore(self.root).items("alice"))
        app.config["TEST_CSRF_PROTECTION"] = True
        self.assertEqual(403, self.client.post(
            f"/automation-v3/{rule.rule_id}/prepare-check-run",
            data={"event_json": '{"name":"manual.invoice.check","approved":true}'},
        ).status_code)
        app.config["TEST_CSRF_PROTECTION"] = False
        with patch("app.v3_automation_routes.enabled", return_value=False):
            self.assertEqual(404, self._preview(rule).status_code)

    def test_task_list_create_permission_is_checked_before_confirmation(self):
        tasks = TodoStore(self.root)
        shared = tasks.create_list({"name": "Private"}, "bob", "bob-private")
        rule = self._rule(actions=[{
            "type": "task.create",
            "payload": {"title": "Review invoice", "values": {"list_id": shared["list_id"]}},
        }])
        response = self._preview(rule)
        self.assertEqual(302, response.status_code)
        self.assertEqual([], tasks.items("alice"))

    def test_deeply_nested_check_json_is_rejected_without_server_error(self):
        rule = self._rule()
        response = self.client.post(
            f"/automation-v3/{rule.rule_id}/prepare-check-run",
            data={"_csrf_token": self._csrf(), "event_json": "[" * 1200 + "0" + "]" * 1200},
        )
        self.assertEqual(302, response.status_code)
        self.assertEqual([], TodoStore(self.root).items("alice"))


if __name__ == "__main__":
    unittest.main()
