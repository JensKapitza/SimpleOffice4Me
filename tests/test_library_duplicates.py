"""Duplicate suggestions for library books (no writes)."""

from __future__ import annotations

import unittest

from app.library_duplicates import normalize_isbn, suggest_library_duplicates


def _book(object_id: str, isbn: str = "", title: str = ""):
    return {
        "object_id": object_id,
        "fields": {"isbn": isbn, "title": title},
    }


class LibraryDuplicatesTest(unittest.TestCase):
    def test_normalizes_separators(self) -> None:
        self.assertEqual(normalize_isbn("978-3-16-148410-0"), "9783161484100")
        self.assertEqual(normalize_isbn("978 3 16 148410 0"), "9783161484100")
        self.assertEqual(normalize_isbn("3-86680-192-X"), "386680192X")
        self.assertEqual(normalize_isbn(""), "")
        self.assertEqual(normalize_isbn("123"), "")

    def test_groups_same_isbn(self) -> None:
        rows = [
            _book("a", "978-3-16-148410-0", "Titel A"),
            _book("b", "9783161484100", "Titel A"),
            _book("c", "978-0-00-000000-0", "Anderes"),
        ]
        proposals = suggest_library_duplicates(rows)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["object_ids"], ["a", "b"])
        self.assertIn("9783161484100", proposals[0]["reason"])

    def test_skips_missing_and_short(self) -> None:
        rows = [_book("a"), _book("b", "123"), _book("c", "978-3-16-148410-0")]
        self.assertEqual(suggest_library_duplicates(rows), [])

    def test_limits_groups(self) -> None:
        rows = [book for i in range(7) for book in
                (_book(f"{i}a", f"978-3-16-148410-{i}", f"T{i}"),
                 _book(f"{i}b", f"978316148410{i}", f"T{i}"))]
        self.assertEqual(len(suggest_library_duplicates(rows)), 5)


if __name__ == "__main__":
    unittest.main()
