"""Action hints for print jobs (no writes)."""

from __future__ import annotations

import unittest

from app.print_job_hints import suggest_print_job_actions


def _job(job_id: str, status: str, printer: str = "Büro-Laser"):
    return {"job_id": job_id, "status": status, "printer_name": printer}


class PrintJobHintsTest(unittest.TestCase):
    def test_failed_first(self) -> None:
        rows = [
            _job("a", "queued"),
            _job("b", "failed"),
            _job("c", "spooled"),
        ]
        proposals = suggest_print_job_actions(rows)
        self.assertEqual([row["job_id"] for row in proposals], ["b", "a"])
        self.assertIn("fehlgeschlagen", proposals[0]["reason"])

    def test_skips_other_statuses(self) -> None:
        rows = [_job("a", "spooled"), _job("b", "done")]
        self.assertEqual(suggest_print_job_actions(rows), [])

    def test_skips_garbage(self) -> None:
        self.assertEqual(suggest_print_job_actions([]), [])
        self.assertEqual(suggest_print_job_actions([None, "x"]), [])

    def test_limits_items(self) -> None:
        rows = [_job(str(i), "failed") for i in range(7)]
        self.assertEqual(len(suggest_print_job_actions(rows)), 5)


if __name__ == "__main__":
    unittest.main()
