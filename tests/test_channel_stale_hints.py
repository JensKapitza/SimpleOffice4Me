"""Stale hints for measurement channels (no writes)."""

from __future__ import annotations

import unittest

from app.channel_stale_hints import suggest_stale_channels


def _row(channel_id: str, name: str, stamps: list):
    return {"id": channel_id, "name": name,
            "samples": [{"measured_at": stamp} for stamp in stamps]}


class ChannelStaleHintsTest(unittest.TestCase):
    def test_flags_empty_and_stale(self) -> None:
        rows = [
            _row("a", "Neu", ["2026-11-04T10:00:00"]),
            _row("b", "Leer", []),
            _row("c", "Alt", ["2026-10-01T10:00:00"]),
        ]
        proposals = suggest_stale_channels(rows, today="2026-11-04")
        self.assertEqual([row["channel_id"] for row in proposals], ["b", "c"])

    def test_skips_recent(self) -> None:
        rows = [_row("a", "Aktiv", ["2026-11-03T10:00:00"])]
        self.assertEqual(
            suggest_stale_channels(rows, today="2026-11-04"), [])

    def test_skips_garbage(self) -> None:
        self.assertEqual(suggest_stale_channels([]), [])
        self.assertEqual(suggest_stale_channels([None, "x"]), [])

    def test_limits_items(self) -> None:
        rows = [_row(str(i), f"K{i}", []) for i in range(7)]
        self.assertEqual(
            len(suggest_stale_channels(rows, today="2026-11-04")), 5
        )


if __name__ == "__main__":
    unittest.main()
