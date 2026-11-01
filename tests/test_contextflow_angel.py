"""Büroengel avatar: opt-out display pref plus suggestion dialogs.

Unit tests cover the HintPrefs avatar flag (default on, legacy rows,
checkbox coercion). Route tests prove the dashboard toggle, the avatar
rendering on both pages, and that switching the avatar off keeps every
action (approve/dismiss/suggest) working.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app import app
from app.contextflow.hints import HintPrefs
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password

from tests import contextflow_fixtures as fx

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=BERLIN)

_CSRF_RE = re.compile(rb'name="_csrf_token" value="([^"]+)"')


class AngelAvatarPrefsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.prefs = HintPrefs(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_default_is_shown(self) -> None:
        self.assertTrue(self.prefs.get("admin")["angel_avatar"])

    def test_checkbox_roundtrip(self) -> None:
        saved = self.prefs.set("admin", "22:00", "07:00", 5, "yes")
        self.assertTrue(saved["angel_avatar"])
        self.assertTrue(self.prefs.get("admin")["angel_avatar"])
        saved = self.prefs.set("admin", "22:00", "07:00", 5, "")
        self.assertFalse(saved["angel_avatar"])
        self.assertFalse(self.prefs.get("admin")["angel_avatar"])

    def test_legacy_call_without_avatar_keeps_default(self) -> None:
        saved = self.prefs.set("admin", "22:00", "07:00", 5)
        self.assertTrue(saved["angel_avatar"])

    def test_legacy_row_without_key_is_shown(self) -> None:
        self.prefs.set("admin", "22:00", "07:00", 5, False)
        import json

        data_path = self.prefs.path
        data = json.loads(data_path.read_text(encoding="utf-8"))
        for row in data["prefs"]:
            row.pop("angel_avatar", None)
        data_path.write_text(json.dumps(data), encoding="utf-8")
        self.assertTrue(self.prefs.get("admin")["angel_avatar"])

    def test_unknown_value_fails_closed_to_hidden(self) -> None:
        saved = self.prefs.set("admin", "22:00", "07:00", 5, "bogus")
        self.assertFalse(saved["angel_avatar"])


class AngelAvatarRouteTests(unittest.TestCase):
    password = "Str0ng-Test-Passwort!"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = dict(app.config)
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
                " VALUES (?,?,?,?,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("admin", hash_password(self.password), "Admin", 1),
            )
            db.commit()
        self.root = Path(self.temp.name) / "documents"
        self.client = app.test_client()
        response = self.client.get("/auth/login")
        token = _CSRF_RE.search(response.data).group(1).decode()
        self.client.post(
            "/auth/login",
            data={"username": "admin", "password": self.password, "_csrf_token": token},
        )

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def _seeded_card(self) -> None:
        # Equal start/end disables quiet hours entirely: the card must
        # render at any time of day (no time-of-day flakiness).
        HintPrefs(self.root).set("admin", "00:00", "00:00", 5)
        contact_id = str(fx.make_contact(self.root, "admin")["contact_id"])
        case_id = fx.make_mail_case(self.root, "admin")
        visit_id = fx.make_visit(self.root, "admin", completed=True)
        fx.confirm_link(
            self.root,
            "admin",
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on=(NOW - timedelta(days=5)).date().isoformat(),
        )

    def _csrf(self, path: str) -> str:
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        match = _CSRF_RE.search(response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def test_dashboard_toggle_defaults_to_checked(self) -> None:
        body = self.client.get("/contextflow/").data.decode("utf-8")
        self.assertIn('name="angel_avatar"', body)
        self.assertIn('name="angel_avatar" value="yes" checked', body)

    def test_dashboard_card_shows_avatar_and_dialog(self) -> None:
        self._seeded_card()
        body = self.client.get("/contextflow/").data.decode("utf-8")
        self.assertIn("bueroengel.svg", body)
        self.assertIn("cf-angel-btn", body)
        self.assertIn("cf-engel-dialog", body)
        self.assertIn("js/angel.js", body)

    def test_avatar_off_keeps_actions(self) -> None:
        self._seeded_card()
        token = self._csrf("/contextflow/")
        response = self.client.post(
            "/contextflow/hint-prefs",
            data={
                "quiet_start": "00:00",
                "quiet_end": "00:00",
                "daily_limit": "5",
                "_csrf_token": token,
            },
        )
        self.assertEqual(302, response.status_code)
        body = self.client.get("/contextflow/").data.decode("utf-8")
        self.assertNotIn("bueroengel.svg", body)
        self.assertNotIn("cf-angel-btn", body)
        self.assertIn('name="angel_avatar"', body)
        self.assertNotIn('name="angel_avatar" value="yes" checked', body)
        # Engel dormancy is display-only: actions stay fully usable.
        self.assertIn("Entwurf vorbereiten", body)
        self.assertIn("Später erinnern", body)
        self.assertIn("Nicht mehr zeigen", body)

    def test_avatar_back_on(self) -> None:
        token = self._csrf("/contextflow/")
        self.client.post(
            "/contextflow/hint-prefs",
            data={
                "quiet_start": "00:00",
                "quiet_end": "00:00",
                "daily_limit": "5",
                "_csrf_token": token,
            },
        )
        token = self._csrf("/contextflow/")
        response = self.client.post(
            "/contextflow/hint-prefs",
            data={
                "quiet_start": "00:00",
                "quiet_end": "00:00",
                "daily_limit": "5",
                "angel_avatar": "yes",
                "_csrf_token": token,
            },
        )
        self.assertEqual(302, response.status_code)
        body = self.client.get("/contextflow/").data.decode("utf-8")
        self.assertIn('name="angel_avatar" value="yes" checked', body)

    def test_inventory_dialog_markup_and_avatar(self) -> None:
        body = self.client.get("/inventory").data.decode("utf-8")
        self.assertIn("engel-suggest-dialog", body)
        self.assertIn("engel-part-name", body)
        self.assertIn("engel-part-type", body)
        self.assertIn("engel-part-note", body)
        self.assertIn("engel-part-tags", body)
        self.assertIn("engel-adopt-parts", body)
        self.assertIn("engel-adopt-all", body)
        self.assertIn("engel-reanalyze", body)
        self.assertIn("engel-reject", body)
        self.assertIn("data-engel-close", body)
        self.assertNotIn('<form method="dialog"', body)
        self.assertIn("bueroengel.svg", body)
        self.assertIn("js/angel.js", body)

    def test_inventory_avatar_follows_pref(self) -> None:
        token = self._csrf("/contextflow/")
        self.client.post(
            "/contextflow/hint-prefs",
            data={
                "quiet_start": "00:00",
                "quiet_end": "00:00",
                "daily_limit": "5",
                "_csrf_token": token,
            },
        )
        body = self.client.get("/inventory").data.decode("utf-8")
        self.assertNotIn("bueroengel.svg", body)
        # The suggestion entry point stays available without the avatar.
        self.assertIn("engel-suggest-box", body)
        self.assertIn("engel-suggest-dialog", body)


if __name__ == "__main__":
    unittest.main()
