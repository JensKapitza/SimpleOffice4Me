"""Review suggestions for soft-deleted documents (no writes)."""

from __future__ import annotations

import unittest

from app.recovery_review import suggest_recovery_review


def _item(document_id: str, origin: str, size: int = 0,
          available: bool = True, deleted_at: str = ""):
    return {
        "document_id": document_id,
        "deleted_from": origin,
        "size": size,
        "available": available,
        "deleted_at": deleted_at,
    }


class RecoveryReviewTest(unittest.TestCase):
    def test_available_largest_first(self) -> None:
        rows = [
            _item("a", "klein.txt", size=100, deleted_at="2026-11-01"),
            _item("b", "gross.bin", size=5000, deleted_at="2026-11-02"),
        ]
        proposals = suggest_recovery_review(rows)
        self.assertEqual(proposals[0]["document_id"], "b")
        self.assertIn("gross.bin", proposals[0]["reason"])

    def test_unavailable_last(self) -> None:
        rows = [
            _item("a", "fehlt.txt", available=False),
            _item("b", "da.txt", size=10, available=True),
        ]
        proposals = suggest_recovery_review(rows)
        self.assertEqual(proposals[0]["document_id"], "b")
        self.assertIn("nicht verfügbar", proposals[-1]["reason"])

    def test_skips_garbage(self) -> None:
        self.assertEqual(suggest_recovery_review([]), [])
        self.assertEqual(suggest_recovery_review([None, "x"]), [])

    def test_limits_items(self) -> None:
        rows = [_item(str(i), f"F{i}.txt", size=i + 1) for i in range(7)]
        self.assertEqual(len(suggest_recovery_review(rows)), 5)


if __name__ == "__main__":
    unittest.main()
