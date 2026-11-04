"""Tests for local smart folder review hints (read-only proposals)."""

import unittest

from app.smart_folder_hints import suggest_folder_review


class SuggestFolderReviewTests(unittest.TestCase):
    def test_empty_active_flagged(self):
        folders = [{"path": "rechnungen", "count": 0, "active": True}]
        hints = suggest_folder_review(folders)
        self.assertEqual(len(hints), 1)
        self.assertIn("keine Treffer", hints[0]["reason"])

    def test_active_with_members_quiet(self):
        folders = [{"path": "rechnungen", "count": 4, "active": True}]
        self.assertEqual(suggest_folder_review(folders), [])

    def test_big_unruled_flagged(self):
        folders = [{"path": "archiv", "count": 25, "active": False}]
        hints = suggest_folder_review(folders)
        self.assertEqual(len(hints), 1)
        self.assertIn("25 Dokumente", hints[0]["reason"])

    def test_small_unruled_quiet(self):
        folders = [{"path": "klein", "count": 3, "active": False}]
        self.assertEqual(suggest_folder_review(folders), [])

    def test_sorted_and_limited(self):
        folders = [{"path": f"ordner-{i:02d}", "count": 0, "active": True} for i in range(7)]
        hints = suggest_folder_review(folders, max_items=3)
        self.assertEqual(len(hints), 3)
        self.assertIn("ordner-00", hints[0]["reason"])

    def test_members_fallback(self):
        folders = [{"path": "alt", "members": [1, 2], "active": False}]
        self.assertEqual(suggest_folder_review(folders), [])
        folders = [{"path": "leer", "members": [], "active": True}]
        hints = suggest_folder_review(folders)
        self.assertEqual(len(hints), 1)


if __name__ == "__main__":
    unittest.main()
