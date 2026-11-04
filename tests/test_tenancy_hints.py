"""Expiry hints for tenancies (no writes)."""

from __future__ import annotations

import unittest

from app.tenancy_hints import suggest_tenancy_renewals


def _group(tenancies):
    return {"name": "Haus A", "units": [{"tenancies": tenancies}]}


def _tenancy(contact: str, ends_on: str):
    return {"contact_name": contact, "ends_on": ends_on}


class TenancyHintsTest(unittest.TestCase):
    def test_ended_first(self) -> None:
        rows = [_group([
            _tenancy("Neu", "2026-11-20"),
            _tenancy("Alt", "2026-10-01"),
        ])]
        proposals = suggest_tenancy_renewals(rows, today="2026-11-04")
        self.assertEqual(proposals[0]["contact"], "Alt")
        self.assertTrue(proposals[0]["ended"])

    def test_ignores_open_ended_and_far_future(self) -> None:
        rows = [_group([
            _tenancy("Offen", ""),
            _tenancy("Fern", "2027-06-01"),
            _tenancy("Bald", "2026-11-20"),
        ])]
        proposals = suggest_tenancy_renewals(rows, today="2026-11-04")
        self.assertEqual([row["contact"] for row in proposals], ["Bald"])

    def test_skips_garbage(self) -> None:
        self.assertEqual(suggest_tenancy_renewals([]), [])
        self.assertEqual(suggest_tenancy_renewals([None, "x"]), [])

    def test_limits_items(self) -> None:
        rows = [_group([_tenancy(f"M{i}", "2026-10-01")]) for i in range(7)]
        self.assertEqual(
            len(suggest_tenancy_renewals(rows, today="2026-11-04")), 5
        )


if __name__ == "__main__":
    unittest.main()
