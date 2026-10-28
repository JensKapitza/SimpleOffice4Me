"""ContextFlow Batch 3: web UI contract tests on the real Flask app.

Run with the project venv (``.venv``) and TEMP/TMP pointing to a short local
path, e.g. ``C:\\Users\\Jens Kapitza\\AppData\\Local\\Temp``.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from app import app
from app.contextflow.case_links import CaseLinkStore
from app.contextflow.evaluator import FollowupEvaluator
from app.contextflow.hints import HintStates
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password
from app.site_visit_store import SiteVisitStore
from app.todo_store import TodoStore

from tests import contextflow_fixtures as fx

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=BERLIN)


class ContextflowWebTest(unittest.TestCase):
    password = "contextflow-web-test-password"

    def setUp(self) -> None:
        # Deterministic test clock: routes call the real Berlin clock via
        # app.contextflow.web._now, but these tests fix NOW=2026-10-29 with
        # completed_on=NOW-5d. Patching _now to NOW keeps every route on the
        # same five-day boundary without touching FOLLOWUP_DAYS or
        # weakening the production freshness rule.
        self._now_patch = mock.patch(
            "app.contextflow.web._now", return_value=NOW
        )
        self._now_patch.start()
        self.addCleanup(self._now_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key)
            for key in (
                "DATABASE",
                "DOCUMENT_ROOT",
                "TESTING",
                "TEST_CSRF_PROTECTION",
                "CONTEXTFLOW_ENABLED",
            )
        }
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            CONTEXTFLOW_ENABLED=True,
        )
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            for username, admin in (("admin", 1), ("berta", 0)):
                db.execute(
                    "INSERT INTO user(username,password,display_name,is_admin,"
                    "is_disabled,auth_version,created_at,updated_at)"
                    " VALUES (?,?,?, ?,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (
                        username,
                        hash_password(self.password),
                        username.title(),
                        admin,
                    ),
                )
            db.commit()
        self.root = Path(self.temp.name) / "documents"
        self.admin = self.login("admin")
        self.berta = self.login("berta")

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def csrf(self, client, path="/contextflow/") -> str:
        response = client.get(path)
        match = re.search(rb'name="_csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match, response.data[:300])
        assert match is not None
        return match.group(1).decode()

    def login(self, username: str):
        client = app.test_client()
        client.post(
            "/auth/login",
            data={
                "username": username,
                "password": self.password,
                "_csrf_token": self.csrf(client, "/auth/login"),
            },
        )
        return client

    def seed_link(self, actor: str = "admin") -> dict:
        contact_id = str(fx.make_contact(self.root, actor)["contact_id"])
        case_id = fx.make_mail_case(self.root, actor)
        visit_id = fx.make_visit(self.root, actor, completed=True)
        return fx.confirm_link(
            self.root,
            actor,
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on=(NOW - timedelta(days=5)).date().isoformat(),
            now=NOW,
        )

    def test_flag_off_404(self) -> None:
        app.config.update(CONTEXTFLOW_ENABLED=False)
        try:
            self.assertEqual(self.admin.get("/contextflow/").status_code, 404)
        finally:
            app.config.update(CONTEXTFLOW_ENABLED=True)

    def test_login_required(self) -> None:
        response = app.test_client().get("/contextflow/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/login", response.headers.get("Location", ""))

    def test_dashboard_happy(self) -> None:
        link = self.seed_link()
        response = self.admin.get("/contextflow/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(fx.CASE_TITLE.encode(), response.data)
        self.assertIn("unbekannt".encode("utf-8"), response.data)
        self.assertIn(link["link_id"].encode(), response.data)

    def test_confirm_link(self) -> None:
        contact_id = str(fx.make_contact(self.root, "admin")["contact_id"])
        case_id = fx.make_mail_case(self.root, "admin")
        visit_id = fx.make_visit(self.root, "admin", completed=True)
        token = self.csrf(self.admin)
        response = self.admin.post(
            "/contextflow/links/confirm",
            data={
                "mail_case_id": case_id,
                "contact_id": contact_id,
                "visit_id": visit_id,
                "invoice_id": "",
                "completed_on": "2026-10-24",
                "_csrf_token": token,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(CaseLinkStore(self.root).list_links("admin")), 1)

    def test_confirm_without_token_rejected(self) -> None:
        response = self.admin.post(
            "/contextflow/links/confirm",
            data={
                "mail_case_id": "x",
                "contact_id": "y",
                "visit_id": "z",
                "completed_on": "2026-10-24",
            },
        )
        self.assertIn(response.status_code, (400, 403))

    def test_confirm_invalid_date_rejected(self) -> None:
        response = self.admin.post(
            "/contextflow/links/confirm",
            data={
                "mail_case_id": "x",
                "contact_id": "y",
                "visit_id": "z",
                "completed_on": "gestern",
                "_csrf_token": self.csrf(self.admin),
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_approve_happy_and_replay(self) -> None:
        link = self.seed_link()
        hint = FollowupEvaluator(self.root).evaluate(
            "admin", link["link_id"], NOW
        )
        assert hint is not None
        token = self.csrf(self.admin)
        payload = {
            "link_id": link["link_id"],
            "expected_hint_id": hint["hint_id"],
            "approved": "yes",
            "_csrf_token": token,
        }
        self.assertEqual(
            self.admin.post("/contextflow/approve", data=payload).status_code, 302
        )
        rows = TodoStore(self.root).items("admin")
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            self.admin.post("/contextflow/approve", data=payload).status_code, 302
        )
        self.assertEqual(len(TodoStore(self.root).items("admin")), 1)

    def test_approve_stale_conflict(self) -> None:
        link = self.seed_link()
        hint = FollowupEvaluator(self.root).evaluate(
            "admin", link["link_id"], NOW
        )
        assert hint is not None
        SiteVisitStore(self.root).add_finding(
            link["visit_id"],
            "admin",
            {"title": "Neu (synthetisch)", "status": "unreviewed"},
        )
        response = self.admin.post(
            "/contextflow/approve",
            data={
                "link_id": link["link_id"],
                "expected_hint_id": hint["hint_id"],
                "approved": "yes",
                "_csrf_token": self.csrf(self.admin),
            },
        )
        self.assertEqual(response.status_code, 409)

    def test_approve_missing_checkbox_rejected(self) -> None:
        link = self.seed_link()
        hint = FollowupEvaluator(self.root).evaluate(
            "admin", link["link_id"], NOW
        )
        assert hint is not None
        response = self.admin.post(
            "/contextflow/approve",
            data={
                "link_id": link["link_id"],
                "expected_hint_id": hint["hint_id"],
                "_csrf_token": self.csrf(self.admin),
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_approve_without_token_rejected(self) -> None:
        response = self.admin.post(
            "/contextflow/approve",
            data={"link_id": "x", "expected_hint_id": "y", "approved": "yes"},
        )
        self.assertIn(response.status_code, (400, 403))

    def test_foreign_user_isolated(self) -> None:
        link = self.seed_link()
        response = self.berta.get("/contextflow/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(fx.CASE_TITLE.encode(), response.data)
        denied = self.berta.post(
            "/contextflow/approve",
            data={
                "link_id": link["link_id"],
                "expected_hint_id": "irrelevant",
                "approved": "yes",
                "_csrf_token": self.csrf(self.berta),
            },
        )
        self.assertIn(denied.status_code, (400, 403, 404))
        self.assertEqual(TodoStore(self.root).items("admin"), [])

    def test_snooze_dismiss(self) -> None:
        link = self.seed_link()
        snoozed = self.admin.post(
            "/contextflow/snooze",
            data={
                "link_id": link["link_id"],
                "until": "2026-11-30",
                "_csrf_token": self.csrf(self.admin),
            },
        )
        self.assertEqual(snoozed.status_code, 302)
        self.assertNotIn(
            fx.CASE_TITLE.encode(), self.admin.get("/contextflow/").data
        )
        self.assertEqual(
            HintStates(self.root).get("admin", link["link_id"])["state"], "snoozed"
        )
        dismissed = self.admin.post(
            "/contextflow/dismiss",
            data={
                "link_id": link["link_id"],
                "_csrf_token": self.csrf(self.admin),
            },
        )
        self.assertEqual(dismissed.status_code, 302)
        self.assertEqual(
            HintStates(self.root).get("admin", link["link_id"])["state"], "dismissed"
        )

    def test_html_escaped_source_text(self) -> None:
        fx.make_contact(
            self.root,
            "admin",
            display_name="<script>alert(1)</script>",
            email="xss@example.test",
        )
        response = self.admin.get("/contextflow/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"&lt;script&gt;", response.data)
        self.assertNotIn(b"<script>alert(1)", response.data)


if __name__ == "__main__":
    unittest.main()
