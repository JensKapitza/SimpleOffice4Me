"""Follow-up suggestions for open mail cases (no writes)."""

from __future__ import annotations

import unittest

from app.mail_case_followups import suggest_case_followups


def _case(case_id: str, title: str, status: str, updated_at: str):
    return {"id": case_id, "title": title, "status": status,
            "updated_at": updated_at}


class MailCaseFollowupsTest(unittest.TestCase):
    def test_waiting_first(self) -> None:
        rows = [
            _case("a", "Alt", "offen", "2026-10-20 10:00:00"),
            _case("b", "Blockiert", "wartet", "2026-11-03 10:00:00"),
        ]
        proposals = suggest_case_followups(rows, today="2026-11-04")
        self.assertEqual(proposals[0]["case_id"], "b")
        self.assertIn("wartet", proposals[0]["reason"])

    def test_skips_closed_and_fresh(self) -> None:
        rows = [
            _case("a", "Erledigt", "erledigt", "2026-10-01 10:00:00"),
            _case("b", "Frisch", "offen", "2026-11-03 10:00:00"),
            _case("c", "Alt", "offen", "2026-10-20 10:00:00"),
        ]
        proposals = suggest_case_followups(rows, today="2026-11-04")
        self.assertEqual([row["case_id"] for row in proposals], ["c"])

    def test_idle_days_reported(self) -> None:
        rows = [_case("a", "Alt", "offen", "2026-10-20 10:00:00")]
        proposals = suggest_case_followups(rows, today="2026-11-04")
        self.assertEqual(proposals[0]["idle_days"], 15)
        self.assertIn("15 Tagen", proposals[0]["reason"])

    def test_limits_items(self) -> None:
        rows = [_case(str(i), f"T{i}", "wartet", "2026-10-01 10:00:00")
                for i in range(7)]
        self.assertEqual(
            len(suggest_case_followups(rows, today="2026-11-04")), 5
        )


if __name__ == "__main__":
    unittest.main()
