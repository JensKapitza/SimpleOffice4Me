"""Continue-reading hints (no writes)."""

from __future__ import annotations

import unittest

from app.reading_hints import suggest_continue_reading


def _book(document_id: str, title: str = "", progress: int = 0):
    return {"document_id": document_id, "title": title,
            "progress": progress}


class ReadingHintsTest(unittest.TestCase):
    def test_flags_unfinished_started(self) -> None:
        rows = [
            _book("a", "Neu", progress=0),
            _book("b", "Mitte", progress=40),
            _book("c", "Fertig", progress=100),
        ]
        proposals = suggest_continue_reading(rows)
        self.assertEqual([row["document_id"] for row in proposals], ["b"])
        self.assertIn("40 %", proposals[0]["reason"])

    def test_sorts_by_progress_desc(self) -> None:
        rows = [
            _book("a", "Wenig", progress=10),
            _book("b", "Viel", progress=90),
        ]
        proposals = suggest_continue_reading(rows)
        self.assertEqual(proposals[0]["document_id"], "b")

    def test_skips_garbage(self) -> None:
        self.assertEqual(suggest_continue_reading([]), [])
        self.assertEqual(suggest_continue_reading([None, "x"]), [])
        self.assertEqual(
            suggest_continue_reading([{"document_id": "a"}]), [])

    def test_limits_items(self) -> None:
        rows = [_book(str(i), f"T{i}", progress=50) for i in range(7)]
        self.assertEqual(len(suggest_continue_reading(rows)), 5)


if __name__ == "__main__":
    unittest.main()
