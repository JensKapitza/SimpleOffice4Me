"""ContextFlow P0-C/P0-F: quiet hours, daily budget, prefs, local rewrite."""

from __future__ import annotations

import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from app import app
from app.contextflow.hints import (
    HintBudget,
    HintPrefs,
    is_quiet_time,
    parse_hhmm,
)
from app.contextflow.local_rewrite import RULE_GREETING, rewrite_hint_lead
from app.contextflow.vision import InferenceTimeoutError, VisionError
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password

from tests import contextflow_fixtures as fx

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=BERLIN)


class QuietTimeTests(unittest.TestCase):
    def test_day_window_boundaries(self) -> None:
        self.assertTrue(is_quiet_time("22:00", "22:00", "07:00"))
        self.assertTrue(is_quiet_time("03:30", "22:00", "07:00"))
        self.assertFalse(is_quiet_time("07:00", "22:00", "07:00"))
        self.assertFalse(is_quiet_time("12:00", "22:00", "07:00"))
        self.assertFalse(is_quiet_time("21:59", "22:00", "07:00"))

    def test_same_day_window(self) -> None:
        self.assertTrue(is_quiet_time("12:00", "11:00", "13:00"))
        self.assertFalse(is_quiet_time("13:00", "11:00", "13:00"))
        self.assertFalse(is_quiet_time("10:59", "11:00", "13:00"))

    def test_equal_bounds_disable_quiet(self) -> None:
        self.assertFalse(is_quiet_time("12:00", "12:00", "12:00"))
        self.assertFalse(is_quiet_time("00:00", "09:00", "09:00"))

    def test_garbage_raises(self) -> None:
        for bad in ("", "24:00", "12:60", "noon", "12", "12:00:00", "1:2:3"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_hhmm(bad)
        with self.assertRaises(ValueError):
            is_quiet_time("xx", "22:00", "07:00")


class HintPrefsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.prefs = HintPrefs(Path(self.temp.name) / "documents")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_defaults_for_unknown_actor(self) -> None:
        self.assertEqual(
            {"quiet_start": "22:00", "quiet_end": "07:00", "daily_limit": 3},
            self.prefs.get("anna"),
        )

    def test_roundtrip_normalizes(self) -> None:
        saved = self.prefs.set("anna", "7:05", "8:00", "5")
        self.assertEqual(
            {"quiet_start": "07:05", "quiet_end": "08:00", "daily_limit": 5},
            saved,
        )
        self.assertEqual(saved, self.prefs.get("anna"))

    def test_invalid_values_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.prefs.set("anna", "25:00", "07:00", 3)
        with self.assertRaises(ValueError):
            self.prefs.set("anna", "22:00", "07:00", 0)
        with self.assertRaises(ValueError):
            self.prefs.set("anna", "22:00", "07:00", 11)
        with self.assertRaises(ValueError):
            self.prefs.set("anna", "22:00", "07:00", "viele")
        with self.assertRaises(ValueError):
            self.prefs.set("anna", "22:00", "07:00", True)

    def test_actor_isolation_and_corrupt_file(self) -> None:
        self.prefs.set("anna", "21:00", "06:00", 2)
        self.assertEqual(2, self.prefs.get("anna")["daily_limit"])
        self.assertEqual(3, self.prefs.get("bob")["daily_limit"])
        self.prefs.path.write_text("{kaputt", encoding="utf-8")
        self.assertEqual("22:00", self.prefs.get("anna")["quiet_start"])


class HintBudgetTests(unittest.TestCase):
    day = "2026-10-29"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.budget = HintBudget(Path(self.temp.name) / "documents")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_allow_up_to_limit_then_digest(self) -> None:
        self.assertTrue(self.budget.allow("anna", "l1", self.day, 2))
        self.budget.record("anna", ["l1"], self.day)
        self.assertTrue(self.budget.allow("anna", "l2", self.day, 2))
        self.budget.record("anna", ["l2"], self.day)
        self.assertFalse(self.budget.allow("anna", "l3", self.day, 2))

    def test_shown_links_stay_allowed(self) -> None:
        self.budget.record("anna", ["l1", "l2"], self.day)
        self.assertTrue(self.budget.allow("anna", "l1", self.day, 2))
        self.assertEqual(["l1", "l2"], self.budget.shown_today("anna", self.day))

    def test_day_rollover_and_actor_isolation(self) -> None:
        self.budget.record("anna", ["l1", "l2"], self.day)
        self.assertTrue(self.budget.allow("anna", "l3", "2026-10-30", 2))
        self.assertTrue(self.budget.allow("bob", "l9", self.day, 2))
        self.assertEqual([], self.budget.shown_today("bob", self.day))

    def test_prune_keeps_seven_days(self) -> None:
        base = datetime(2026, 10, 1, tzinfo=BERLIN).date()
        for offset in range(10):
            day = (base + timedelta(days=offset)).isoformat()
            self.budget.record("anna", [f"l{offset}"], day)
        self.assertEqual([], self.budget.shown_today("anna", "2026-10-01"))
        self.assertEqual(["l9"], self.budget.shown_today("anna", "2026-10-10"))

    def test_garbage_fails_closed(self) -> None:
        self.assertFalse(self.budget.allow("anna", "", self.day, 3))
        self.assertFalse(self.budget.allow("anna", "l1", "gestern", 3))
        self.assertFalse(self.budget.allow("anna", "l1", self.day, 0))
        with self.assertRaises(ValueError):
            self.budget.record("anna", ["l1"], "gestern")


class RewriteTests(unittest.TestCase):
    settings_local = {
        "provider": "local",
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "llama3.2:1b",
        "ollama_timeout": 60,
    }

    def test_non_local_provider_keeps_rule_text(self) -> None:
        for provider in ("rules_only", "mock", "meta", ""):
            settings = dict(self.settings_local, provider=provider or "rules_only")
            with (
                self.subTest(provider=provider),
                mock.patch(
                    "app.contextflow.local_rewrite.complete_text"
                ) as completed,
            ):
                text, rewritten = rewrite_hint_lead(
                    RULE_GREETING, settings=settings, root="/tmp/none"
                )
                self.assertEqual(RULE_GREETING, text)
                self.assertFalse(rewritten)
                completed.assert_not_called()

    def test_local_success_rewrites(self) -> None:
        with mock.patch(
            "app.contextflow.local_rewrite.complete_text",
            return_value={"text": "  Ruhig Blut – der Engel hat alles im Blick.  "},
        ):
            text, rewritten = rewrite_hint_lead(
                RULE_GREETING, settings=self.settings_local, root="/tmp/none"
            )
        self.assertTrue(rewritten)
        self.assertEqual("Ruhig Blut – der Engel hat alles im Blick.", text)

    def test_local_failures_fall_back_to_rule_text(self) -> None:
        failures = [
            VisionError("kaputt"),
            InferenceTimeoutError("zu langsam"),
            OSError("down"),
            ValueError("kein JSON"),
            KeyError("x"),
        ]
        for failure in failures:
            with (
                self.subTest(failure=type(failure).__name__),
                mock.patch(
                    "app.contextflow.local_rewrite.complete_text",
                    side_effect=failure,
                ),
            ):
                text, rewritten = rewrite_hint_lead(
                    RULE_GREETING, settings=self.settings_local, root="/tmp/none"
                )
                self.assertEqual(RULE_GREETING, text)
                self.assertFalse(rewritten)

    def test_empty_or_oversize_answer_rejected(self) -> None:
        for answer in ("", "   ", "x" * 281):
            with (
                self.subTest(answer=answer[:8]),
                mock.patch(
                    "app.contextflow.local_rewrite.complete_text",
                    return_value={"text": answer},
                ),
            ):
                text, rewritten = rewrite_hint_lead(
                    RULE_GREETING, settings=self.settings_local, root="/tmp/none"
                )
                self.assertEqual(RULE_GREETING, text)
                self.assertFalse(rewritten)

    def test_empty_input_stays_empty(self) -> None:
        text, rewritten = rewrite_hint_lead(
            "  ", settings=self.settings_local, root="/tmp/none"
        )
        self.assertEqual("", text)
        self.assertFalse(rewritten)


class HintPrefsRouteTests(unittest.TestCase):
    password = "contextflow-hintprefs-test-password"

    def setUp(self) -> None:
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
            db.execute(
                "INSERT INTO user(username,password,display_name,is_admin,"
                "is_disabled,auth_version,created_at,updated_at)"
                " VALUES (?,?,?, ?,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("admin", hash_password(self.password), "Admin", 1),
            )
            db.commit()
        self.root = Path(self.temp.name) / "documents"
        self.admin = self.login("admin")

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def login(self, username: str):
        client = app.test_client()
        page = client.get("/auth/login")
        match = re.search(rb'name="_csrf_token" value="([^"]+)"', page.data)
        self.assertIsNotNone(match)
        assert match is not None
        client.post(
            "/auth/login",
            data={
                "username": username,
                "password": self.password,
                "_csrf_token": match.group(1).decode(),
            },
        )
        return client

    def csrf(self) -> str:
        response = self.admin.get("/contextflow/")
        match = re.search(
            rb'name="_csrf_token" value="([^"]+)"', response.data
        )
        self.assertIsNotNone(match, response.data[:300])
        assert match is not None
        return match.group(1).decode()

    def seed_link(self) -> dict:
        contact_id = str(fx.make_contact(self.root, "admin")["contact_id"])
        case_id = fx.make_mail_case(self.root, "admin")
        visit_id = fx.make_visit(self.root, "admin", completed=True)
        return fx.confirm_link(
            self.root,
            "admin",
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on=(NOW - timedelta(days=5)).date().isoformat(),
            now=NOW,
        )

    def test_prefs_roundtrip(self) -> None:
        response = self.admin.post(
            "/contextflow/hint-prefs",
            data={
                "quiet_start": "21:00",
                "quiet_end": "06:30",
                "daily_limit": "2",
                "_csrf_token": self.csrf(),
            },
        )
        self.assertEqual(302, response.status_code)
        stored = HintPrefs(self.root).get("admin")
        self.assertEqual("21:00", stored["quiet_start"])
        self.assertEqual("06:30", stored["quiet_end"])
        self.assertEqual(2, stored["daily_limit"])
        page = self.admin.get("/contextflow/")
        self.assertIn(b'value="21:00"', page.data)
        self.assertIn(b'value="06:30"', page.data)

    def test_prefs_invalid_rejected(self) -> None:
        response = self.admin.post(
            "/contextflow/hint-prefs",
            data={
                "quiet_start": "alles",
                "quiet_end": "07:00",
                "daily_limit": "3",
                "_csrf_token": self.csrf(),
            },
        )
        self.assertEqual(400, response.status_code)
        self.assertEqual(3, HintPrefs(self.root).get("admin")["daily_limit"])

    def test_dashboard_default_shows_rule_greeting(self) -> None:
        self.seed_link()
        page = self.admin.get("/contextflow/")
        self.assertEqual(200, page.status_code)
        body = page.get_data(as_text=True)
        self.assertIn(RULE_GREETING, body)
        self.assertNotIn("Ruhezeit aktiv", body)
        self.assertNotIn("Tageslimit erreicht", body)

    def test_dashboard_quiet_hides_cards_into_digest(self) -> None:
        link = self.seed_link()
        HintPrefs(self.root).set("admin", "11:00", "13:00", 3)
        page = self.admin.get("/contextflow/")
        self.assertEqual(200, page.status_code)
        body = page.get_data(as_text=True)
        self.assertIn("Ruhezeit aktiv", body)
        self.assertIn("1 Hinweis(e) warten in der Sammelliste", body)
        self.assertNotIn(link["link_id"], body)

    def test_dashboard_budget_overflow_shows_digest(self) -> None:
        link = self.seed_link()
        HintBudget(self.root).record(
            "admin", ["alt-1", "alt-2", "alt-3"], "2026-10-29"
        )
        page = self.admin.get("/contextflow/")
        self.assertEqual(200, page.status_code)
        body = page.get_data(as_text=True)
        self.assertIn("Tageslimit erreicht", body)
        self.assertIn("1 weitere Hinweise in der Sammelliste", body)
        self.assertNotIn(link["link_id"], body)

    def test_dashboard_rewritten_greeting(self) -> None:
        self.seed_link()
        with mock.patch(
            "app.contextflow.web.rewrite_hint_lead",
            return_value=("Engel grüßt freundlich.", True),
        ):
            page = self.admin.get("/contextflow/")
        self.assertIn("Engel grüßt freundlich.", page.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
