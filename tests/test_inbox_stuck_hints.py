"""Attention hints for stuck inbox entries (no writes)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.inbox_stuck_hints import suggest_inbox_attention


def _item(item_id: str, status: str, name: str = "Datei.pdf",
          source: str = "web", error: str = "", malware: str = ""):
    return {
        "item_id": item_id,
        "status": status,
        "original_name": name,
        "source": source,
        "error": error,
        "malware_status": malware,
    }


class InboxStuckHintsTest(unittest.TestCase):
    def test_failed_first_with_error(self) -> None:
        rows = [
            _item("a", "review"),
            _item("b", "quarantined", malware="suspicious"),
            _item("c", "failed", error="Zeitüberschreitung beim Import"),
        ]
        proposals = suggest_inbox_attention(rows)
        self.assertEqual(
            [row["item_id"] for row in proposals], ["c", "b"])
        self.assertIn("Zeitüberschreitung", proposals[0]["reason"])
        self.assertIn("Quarantäne", proposals[1]["reason"])

    def test_skips_terminal_and_active(self) -> None:
        rows = [
            _item("a", "accepted"),
            _item("b", "rejected"),
            _item("c", "received"),
            _item("d", "failed"),
        ]
        self.assertEqual(
            [row["item_id"] for row in suggest_inbox_attention(rows)],
            ["d"],
        )

    def test_accepts_objects(self) -> None:
        rows = [SimpleNamespace(
            item_id="a", status="failed", original_name="Scan.pdf",
            source="s3", error="", malware_status="")]
        proposals = suggest_inbox_attention(rows)
        self.assertEqual(len(proposals), 1)
        self.assertIn("Scan.pdf", proposals[0]["reason"])

    def test_limits_items(self) -> None:
        rows = [_item(str(i), "failed", name=f"F{i}") for i in range(7)]
        self.assertEqual(len(suggest_inbox_attention(rows)), 5)


if __name__ == "__main__":
    unittest.main()
