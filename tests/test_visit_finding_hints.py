"""Finding hints for open site visits (no writes)."""

from __future__ import annotations

import unittest

from app.visit_finding_hints import suggest_visit_findings


def _visit(visit_id: str, title: str, status: str = "draft",
           assets: int = 0, findings: int = 0):
    return {
        "visit_id": visit_id,
        "title": title,
        "status": status,
        "asset_count": assets,
        "finding_count": findings,
    }


class VisitFindingHintsTest(unittest.TestCase):
    def test_flags_devices_without_findings(self) -> None:
        rows = [
            _visit("a", "Werkstatt", assets=3, findings=0),
            _visit("b", "Büro", assets=1, findings=1),
        ]
        proposals = suggest_visit_findings(rows)
        self.assertEqual([row["visit_id"] for row in proposals], ["a"])
        self.assertIn("kein Befund", proposals[0]["reason"])

    def test_skips_closed_and_empty(self) -> None:
        rows = [
            _visit("a", "Alt", status="completed", assets=3, findings=0),
            _visit("b", "Leer", assets=0, findings=0),
            _visit("c", "Offen", assets=2, findings=0),
        ]
        proposals = suggest_visit_findings(rows)
        self.assertEqual([row["visit_id"] for row in proposals], ["c"])

    def test_sorts_by_asset_count(self) -> None:
        rows = [
            _visit("a", "Klein", assets=1, findings=0),
            _visit("b", "Groß", assets=5, findings=0),
        ]
        proposals = suggest_visit_findings(rows)
        self.assertEqual(proposals[0]["visit_id"], "b")

    def test_limits_items(self) -> None:
        rows = [_visit(str(i), f"T{i}", assets=1, findings=0)
                for i in range(7)]
        self.assertEqual(len(suggest_visit_findings(rows)), 5)


if __name__ == "__main__":
    unittest.main()
