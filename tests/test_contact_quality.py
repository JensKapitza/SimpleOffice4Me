"""Quality suggestions for contacts (no writes)."""

from __future__ import annotations

import unittest

from app.contact_quality import (
    normalize_person_name,
    normalize_street,
    suggest_contact_quality,
)


def _contact(contact_id: str, display_name: str = "",
             addresses: list | None = None):
    return {
        "contact_id": contact_id,
        "fields": {"display_name": display_name},
        "addresses": [{"value": value} for value in (addresses or [])],
    }


class ContactQualityTest(unittest.TestCase):
    def test_name_whitespace_and_case(self) -> None:
        self.assertEqual(normalize_person_name("  Peter   Meier "), "Peter Meier")
        self.assertEqual(normalize_person_name("PETER MEIER"), "Peter Meier")
        self.assertEqual(normalize_person_name("peter meier"), "Peter Meier")
        self.assertEqual(normalize_person_name("Peter Meier"), "")

    def test_name_reversed(self) -> None:
        self.assertEqual(normalize_person_name("Meier, Peter"), "Peter Meier")

    def test_street_abbreviation(self) -> None:
        self.assertEqual(normalize_street("Hauptstr. 12"), "Hauptstraße 12")
        self.assertEqual(normalize_street("Hauptstrasse 12"), "Hauptstraße 12")
        self.assertEqual(normalize_street("Hauptstraße 12"), "")

    def test_fuzzy_pair_found(self) -> None:
        rows = [
            _contact("a", "Peter Meier"),
            _contact("b", "Peter Meyer"),
            _contact("c", "Anna Schmidt"),
        ]
        proposals = suggest_contact_quality(rows)
        pairs = [row for row in proposals if row["kind"] == "fuzzy_pair"]
        self.assertEqual(len(pairs), 1)
        self.assertIn("Dublette", pairs[0]["reason"])

    def test_no_false_positive_on_short_names(self) -> None:
        rows = [_contact("a", "Li Ma"), _contact("b", "Li Mo")]
        self.assertEqual(
            [row for row in suggest_contact_quality(rows)
             if row["kind"] == "fuzzy_pair"], []
        )

    def test_limits_items(self) -> None:
        rows = [_contact(str(i), "  NAME %d " % i) for i in range(12)]
        proposals = suggest_contact_quality(rows)
        self.assertLessEqual(len(proposals), 10)


if __name__ == "__main__":
    unittest.main()
