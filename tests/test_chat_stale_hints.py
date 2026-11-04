"""Archive hints for inactive chat rooms (no writes)."""

from __future__ import annotations

import unittest

from app.chat_stale_hints import suggest_stale_rooms

NOW = 1793481600  # 2026-11-04 00:00:00 UTC
DAY = 86400


def _room(room_id: str, title: str = "", updated_at: int = 0,
          archived: bool = False):
    return {
        "room_id": room_id,
        "title": title,
        "updated_at": updated_at,
        "archived": archived,
    }


class ChatStaleHintsTest(unittest.TestCase):
    def test_flags_idle_rooms(self) -> None:
        rows = [
            _room("a", "Neu", updated_at=NOW - DAY),
            _room("b", "Alt", updated_at=NOW - 40 * DAY),
        ]
        proposals = suggest_stale_rooms(rows, now=NOW)
        self.assertEqual([row["room_id"] for row in proposals], ["b"])
        self.assertIn("40 Tagen", proposals[0]["reason"])

    def test_skips_archived_and_fresh(self) -> None:
        rows = [
            _room("a", "Archiv", updated_at=NOW - 90 * DAY, archived=True),
            _room("b", "Frisch", updated_at=NOW - 2 * DAY),
            _room("c", "Alt", updated_at=NOW - 31 * DAY),
        ]
        self.assertEqual(
            [row["room_id"] for row in suggest_stale_rooms(rows, now=NOW)],
            ["c"],
        )

    def test_skips_garbage(self) -> None:
        self.assertEqual(suggest_stale_rooms([]), [])
        self.assertEqual(suggest_stale_rooms([None, "x"]), [])

    def test_limits_items(self) -> None:
        rows = [_room(str(i), f"R{i}", updated_at=NOW - 60 * DAY)
                for i in range(7)]
        self.assertEqual(len(suggest_stale_rooms(rows, now=NOW)), 5)


if __name__ == "__main__":
    unittest.main()
