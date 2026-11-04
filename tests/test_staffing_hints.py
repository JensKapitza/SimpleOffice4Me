"""Schedule and break hints (no writes)."""

from __future__ import annotations

import unittest

from app.staffing_hints import suggest_staffing_gaps


def _employee(employee_id: str, name: str, week_hours: float = 40.0,
              private: bool = True, today: dict | None = None):
    return {
        "id": employee_id,
        "name": name,
        "week_hours": week_hours,
        "private_time_visible": private,
        "today": today if today is not None else {"open": False,
                                                  "compliant": True,
                                                  "work_minutes": 480},
    }


class StaffingHintsTest(unittest.TestCase):
    def test_missing_schedule_first(self) -> None:
        rows = [
            _employee("a", "Planlos", week_hours=0.0),
            _employee("b", "Müde", today={"open": False,
                                           "compliant": False,
                                           "work_minutes": 500}),
        ]
        proposals = suggest_staffing_gaps(rows)
        self.assertEqual(proposals[0]["employee_id"], "a")
        self.assertIn("Wochenplan", proposals[0]["reason"])

    def test_break_shortfall(self) -> None:
        rows = [_employee("a", "Müde", today={"open": False,
                                              "compliant": False,
                                              "work_minutes": 500})]
        proposals = suggest_staffing_gaps(rows)
        self.assertEqual(len(proposals), 1)
        self.assertIn("Pause", proposals[0]["reason"])

    def test_respects_privacy_and_open_days(self) -> None:
        rows = [
            _employee("a", "Fremd", private=False,
                      today={"open": False, "compliant": False,
                             "work_minutes": 500}),
            _employee("b", "Aktiv", today={"open": True, "compliant": False,
                                           "work_minutes": 500}),
            _employee("c", "Ok", today={"open": False, "compliant": True,
                                        "work_minutes": 480}),
        ]
        self.assertEqual(suggest_staffing_gaps(rows), [])

    def test_limits_items(self) -> None:
        rows = [_employee(str(i), f"M{i}", week_hours=0.0) for i in range(7)]
        self.assertEqual(len(suggest_staffing_gaps(rows)), 5)


if __name__ == "__main__":
    unittest.main()
