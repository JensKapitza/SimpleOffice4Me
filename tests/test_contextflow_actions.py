"""ContextFlow Batch 3: approved, idempotent task creation.

Run with the project venv (``.venv``) and TEMP/TMP pointing to a short local
path, e.g. ``C:\\Users\\Jens Kapitza\\AppData\\Local\\Temp``.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.contextflow.actions import ApprovalStale, approve_task
from app.contextflow.evaluator import FollowupEvaluator
from app.site_visit_store import SiteVisitStore
from app.todo_store import TodoStore

from tests import contextflow_fixtures as fx

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=BERLIN)


class ApproveTaskTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        contact_id = str(fx.make_contact(self.root)["contact_id"])
        case_id = fx.make_mail_case(self.root)
        visit_id = fx.make_visit(self.root, completed=True)
        SiteVisitStore(self.root).add_finding(
            visit_id,
            fx.OWNER,
            {"title": "Zweiter Befund (synthetisch)", "status": "unreviewed"},
        )
        self.link = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on=(NOW - timedelta(days=5)).date().isoformat(),
            now=NOW,
        )
        hint = FollowupEvaluator(self.root).evaluate(
            fx.OWNER, self.link["link_id"], NOW
        )
        assert hint is not None
        self.hint_id = str(hint["hint_id"])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _approve(self, **overrides):
        params = {
            "actor": fx.OWNER,
            "link_id": self.link["link_id"],
            "expected_hint_id": self.hint_id,
            "approved": True,
            "now": NOW,
            "root": self.root,
        }
        params.update(overrides)
        return approve_task(**params)

    def test_happy_path_creates_real_task(self) -> None:
        result = self._approve()
        self.assertTrue(result["created"])
        task = result["task"]
        self.assertTrue(task["id"])
        self.assertIn("Befunde", task["title"])
        stored = TodoStore(self.root).get_resource(result["resource"], fx.OWNER)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored["id"], task["id"])
        self.assertTrue(stored["uid"].startswith("cf-"))

    def test_replay_and_restart_no_duplicates(self) -> None:
        first = self._approve()
        second = self._approve()
        self.assertFalse(second["created"])
        self.assertEqual(second["task"]["id"], first["task"]["id"])
        fresh = approve_task(
            fx.OWNER,
            self.link["link_id"],
            self.hint_id,
            True,
            NOW,
            self.root,
        )
        self.assertFalse(fresh["created"])
        rows = TodoStore(self.root).items(fx.OWNER)
        self.assertEqual(len(rows), 1)

    def test_stale_hint_rejected(self) -> None:
        SiteVisitStore(self.root).add_finding(
            str(self.link["visit_id"]),
            fx.OWNER,
            {"title": "Dritter Befund (synthetisch)", "status": "unreviewed"},
        )
        with self.assertRaises(ApprovalStale):
            self._approve()

    def test_denied_actor_rejected(self) -> None:
        with self.assertRaises((PermissionError, ValueError)):
            self._approve(actor=fx.FOREIGN)

    def test_missing_approval_rejected(self) -> None:
        for bad in (False, None, "yes", 1):
            with self.assertRaises(ValueError):
                self._approve(approved=bad)  # type: ignore[arg-type]

    def test_naive_now_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._approve(now=datetime(2026, 10, 29, 12, 0))


if __name__ == "__main__":
    unittest.main()
