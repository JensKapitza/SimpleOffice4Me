"""Duplicate suggestions for catalog objects (no writes)."""

from __future__ import annotations

import unittest

from app.object_duplicates import normalize_object_name, suggest_object_duplicates


def _item(object_id: str, name: str, sequence_id: int = 0):
    return {"object_id": object_id, "name": name, "sequence_id": sequence_id}


class ObjectDuplicatesTest(unittest.TestCase):
    def test_normalizes_case_and_whitespace(self) -> None:
        self.assertEqual(normalize_object_name("  Bohrer  X "), "bohrer x")
        self.assertEqual(normalize_object_name(""), "")

    def test_groups_duplicates_keeper_first(self) -> None:
        rows = [
            _item("b", "Bohrer", sequence_id=2),
            _item("a", "  bohrer ", sequence_id=1),
            _item("c", "Hammer", sequence_id=3),
        ]
        proposals = suggest_object_duplicates(rows)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["keep_object_id"], "a")
        self.assertEqual(proposals[0]["duplicate_object_ids"], ["b"])
        self.assertIn("bohrer", proposals[0]["reason"])

    def test_skips_unique_and_empty(self) -> None:
        rows = [_item("a", "Bohrer"), _item("b", "Hammer"), _item("c", "")]
        self.assertEqual(suggest_object_duplicates(rows), [])

    def test_limits_groups(self) -> None:
        rows = [item for i in range(7) for item in
                (_item(f"{i}a", f"Teil {i}"), _item(f"{i}b", f"teil  {i}"))]
        self.assertEqual(len(suggest_object_duplicates(rows)), 5)


if __name__ == "__main__":
    unittest.main()
