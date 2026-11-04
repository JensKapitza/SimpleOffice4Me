"""Overlap suggestions for active calendar events (no writes)."""

from __future__ import annotations

import unittest

from app.calendar_overlaps import suggest_calendar_overlaps


def _event(event_id: str, title: str, start: str, end: str,
           status: str = "active"):
    return {
        "event_id": event_id,
        "title": title,
        "start": start,
        "end": end,
        "status": status,
    }


DAY = "2026-11-04"


class CalendarOverlapsTest(unittest.TestCase):
    def test_detects_overlap(self) -> None:
        events = [
            _event("a", "Standup", f"{DAY}T09:00:00", f"{DAY}T10:00:00"),
            _event("b", "Review", f"{DAY}T09:30:00", f"{DAY}T10:30:00"),
        ]
        proposals = suggest_calendar_overlaps(events)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["event_ids"], ["a", "b"])
        self.assertIn("Standup", proposals[0]["reason"])

    def test_ignores_touching_and_cancelled(self) -> None:
        events = [
            _event("a", "A", f"{DAY}T09:00:00", f"{DAY}T10:00:00"),
            _event("b", "B", f"{DAY}T10:00:00", f"{DAY}T11:00:00"),
            _event("c", "C", f"{DAY}T09:30:00", f"{DAY}T09:45:00",
                   status="cancelled"),
        ]
        self.assertEqual(suggest_calendar_overlaps(events), [])

    def test_skips_missing_times(self) -> None:
        events = [
            _event("a", "Ohne Zeit", "", ""),
            _event("b", "Mit Zeit", f"{DAY}T09:00:00", f"{DAY}T10:00:00"),
        ]
        self.assertEqual(suggest_calendar_overlaps(events), [])

    def test_limits_items(self) -> None:
        events = [
            _event(str(i), f"T{i}", f"{DAY}T09:00:00", f"{DAY}T12:00:00")
            for i in range(7)
        ]
        self.assertEqual(len(suggest_calendar_overlaps(events)), 5)


if __name__ == "__main__":
    unittest.main()
