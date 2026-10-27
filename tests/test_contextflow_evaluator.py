"""ContextFlow Batch 2: deterministic 5-day follow-up evaluation.

Run with the project venv (``.venv``) and TEMP/TMP pointing to a short local
path, e.g. ``C:\\Users\\Jens Kapitza\\AppData\\Local\\Temp``.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from app.contextflow.evaluator import FollowupEvaluator
from app.site_visit_store import SiteVisitStore

from tests import contextflow_fixtures as fx

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=BERLIN)


class FollowupEvaluatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.evaluator = FollowupEvaluator(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _link(self, days_ago: int, **kwargs) -> dict:
        contact_id = str(fx.make_contact(self.root)["contact_id"])
        case_id = fx.make_mail_case(self.root)
        visit_id = fx.make_visit(self.root, completed=True, **kwargs)
        completed_on = (NOW - timedelta(days=days_ago)).date().isoformat()
        return fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on=completed_on,
            now=NOW,
        )

    def test_fires_at_five_days_stable(self) -> None:
        link = self._link(5)
        first = self.evaluator.evaluate(fx.OWNER, link["link_id"], NOW)
        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual(first["signal"], "visit_follow_up")
        self.assertEqual(first["reason_code"], "unreviewed_findings")
        self.assertEqual(first["certainty"], "verified")
        self.assertEqual(first["days_since_completion"], 5)
        self.assertEqual(len(first["pending_finding_ids"]), 1)
        self.assertEqual(first["proposals"][0]["type"], "task.create")
        self.assertTrue(first["proposals"][0]["approval_required"])
        types = {item["type"] for item in first["evidence"]}
        self.assertEqual(types, {"mail_case", "contact", "site_visit"})
        for item in first["evidence"]:
            self.assertTrue(item["id"])
            self.assertTrue(item["revision"])
        second = self.evaluator.evaluate(fx.OWNER, link["link_id"], NOW)
        assert second is not None
        self.assertEqual(second["hint_id"], first["hint_id"])

    def test_silent_before_five_days(self) -> None:
        self.assertIsNone(self.evaluator.evaluate(fx.OWNER, self._link(4)["link_id"], NOW))
        self.assertIsNone(self.evaluator.evaluate(fx.OWNER, self._link(0)["link_id"], NOW))

    def test_no_hint_when_findings_done(self) -> None:
        link = self._link(5, finding_status="confirmed")
        self.assertIsNone(self.evaluator.evaluate(fx.OWNER, link["link_id"], NOW))

    def test_no_hint_when_visit_reopened(self) -> None:
        contact_id = str(fx.make_contact(self.root)["contact_id"])
        case_id = fx.make_mail_case(self.root)
        visit_id = fx.make_visit(self.root, completed=True)
        link = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on=(NOW - timedelta(days=5)).date().isoformat(),
            now=NOW,
        )
        SiteVisitStore(self.root).update(visit_id, fx.OWNER, {"status": "draft"})
        self.assertIsNone(self.evaluator.evaluate(fx.OWNER, link["link_id"], NOW))

    def test_dst_week_counts_calendar_days(self) -> None:
        contact_id = str(fx.make_contact(self.root)["contact_id"])
        case_id = fx.make_mail_case(self.root)
        visit_id = fx.make_visit(
            self.root, completed=True, visit_date="2026-10-24T10:00"
        )
        link = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on="2026-10-24",
        )
        hint = self.evaluator.evaluate(fx.OWNER, link["link_id"], NOW)
        assert hint is not None
        self.assertEqual(hint["days_since_completion"], 5)

    def test_foreign_actor_denied(self) -> None:
        link = self._link(5)
        with self.assertRaises(ValueError):
            self.evaluator.evaluate(fx.FOREIGN, link["link_id"], NOW)

    def test_revoked_rights_raise_fresh(self) -> None:
        link = self._link(5)
        with mock.patch(
            "app.contextflow.adapters.ContextflowReads.read_visit",
            side_effect=PermissionError("rights revoked"),
        ):
            with self.assertRaises(PermissionError):
                self.evaluator.evaluate(fx.OWNER, link["link_id"], NOW)

    def test_invoice_draft_vs_unknown(self) -> None:
        contact_id = str(fx.make_contact(self.root)["contact_id"])
        case_id = fx.make_mail_case(self.root)
        visit_id = fx.make_visit(self.root, completed=True)
        completed_on = (NOW - timedelta(days=5)).date().isoformat()
        fx.make_invoice_row(self.root, contact_id, "cf-inv-010")
        with_draft = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on=completed_on,
            invoice_id="cf-inv-010",
            now=NOW,
        )
        hint = self.evaluator.evaluate(fx.OWNER, with_draft["link_id"], NOW)
        assert hint is not None
        self.assertEqual(hint["invoice"]["state"], "linked")
        self.assertEqual(hint["invoice"]["invoice_id"], "cf-inv-010")
        self.assertIn("invoice", {item["type"] for item in hint["evidence"]})

        visit_id2 = fx.make_visit(self.root, completed=True)
        without_ref = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id2,
            completed_on=completed_on,
            now=NOW,
        )
        plain = self.evaluator.evaluate(fx.OWNER, without_ref["link_id"], NOW)
        assert plain is not None
        self.assertEqual(plain["invoice"]["state"], "unknown")
        self.assertNotIn("invoice", {item["type"] for item in plain["evidence"]})

    def test_fingerprint_changes_on_source_change(self) -> None:
        contact_id = str(fx.make_contact(self.root)["contact_id"])
        case_id = fx.make_mail_case(self.root)
        visit_id = fx.make_visit(self.root, completed=True)
        link = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on=(NOW - timedelta(days=5)).date().isoformat(),
            now=NOW,
        )
        before = self.evaluator.evaluate(fx.OWNER, link["link_id"], NOW)
        assert before is not None
        SiteVisitStore(self.root).add_finding(
            visit_id,
            fx.OWNER,
            {"title": "Zweiter Befund (synthetisch)", "status": "unreviewed"},
        )
        after = self.evaluator.evaluate(fx.OWNER, link["link_id"], NOW)
        assert after is not None
        self.assertNotEqual(before["hint_id"], after["hint_id"])
        self.assertEqual(len(after["pending_finding_ids"]), 2)

    def test_naive_now_rejected(self) -> None:
        link = self._link(5)
        with self.assertRaises(ValueError):
            self.evaluator.evaluate(
                fx.OWNER, link["link_id"], datetime(2026, 10, 29, 12, 0)
            )
        with self.assertRaises(ValueError):
            self.evaluator.evaluate(fx.OWNER, link["link_id"], "2026-10-29")  # type: ignore[arg-type]

    def test_hint_references_linked_objects_not_names(self) -> None:
        fx.make_contact(self.root)
        second_id = str(
            fx.make_contact(self.root, email="mueller-zweit@example.test")[
                "contact_id"
            ]
        )
        case_id = fx.make_mail_case(self.root)
        visit_id = fx.make_visit(self.root, completed=True)
        link = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=case_id,
            contact_id=second_id,
            visit_id=visit_id,
            completed_on=(NOW - timedelta(days=5)).date().isoformat(),
            now=NOW,
        )
        hint = self.evaluator.evaluate(fx.OWNER, link["link_id"], NOW)
        assert hint is not None
        contact_refs = [
            item for item in hint["evidence"] if item["type"] == "contact"
        ]
        self.assertEqual([item["id"] for item in contact_refs], [second_id])


    def test_evidence_revisions_deterministic(self) -> None:
        link = self._link(5)
        first = FollowupEvaluator(self.root).evaluate(fx.OWNER, link["link_id"], NOW)
        second = FollowupEvaluator(self.root).evaluate(fx.OWNER, link["link_id"], NOW)
        assert first is not None and second is not None
        self.assertEqual(first["hint_id"], second["hint_id"])
        self.assertEqual(len(first["evidence"]), len(second["evidence"]))
        for left, right in zip(first["evidence"], second["evidence"]):
            self.assertEqual(left["type"], right["type"])
            self.assertEqual(left["id"], right["id"])
            self.assertEqual(left["revision"], right["revision"])


if __name__ == "__main__":
    unittest.main()
