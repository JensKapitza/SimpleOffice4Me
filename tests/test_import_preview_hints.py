"""Tests for local import preview hints (read-only proposals)."""

import unittest

from app.import_preview_hints import suggest_import_fixes


def _row(line, email="", error=""):
    return {"line": line, "fields": {"email": email}, "error": error}


class SuggestImportFixesTests(unittest.TestCase):
    def test_invalid_lines_listed(self):
        preview = {"rows": [_row(2, "a@x.de"), _row(3, error="Name fehlt"), _row(5, error="Mail ungültig")]}
        hints = suggest_import_fixes(preview)
        self.assertEqual(len(hints), 1)
        self.assertIn("3", hints[0]["reason"])
        self.assertIn("5", hints[0]["reason"])

    def test_clean_file_quiet(self):
        preview = {"rows": [_row(2, "a@x.de"), _row(3, "b@x.de")]}
        self.assertEqual(suggest_import_fixes(preview), [])

    def test_within_file_duplicate_flagged(self):
        preview = {"rows": [_row(2, "a@x.de"), _row(4, "A@X.DE"), _row(5, "b@x.de")]}
        hints = suggest_import_fixes(preview)
        self.assertEqual(len(hints), 1)
        self.assertIn("2", hints[0]["reason"])
        self.assertIn("4", hints[0]["reason"])

    def test_case_insensitive_and_single_mail_ignored(self):
        preview = {"rows": [_row(2, "a@x.de"), _row(3, "")]}
        self.assertEqual(suggest_import_fixes(preview), [])

    def test_limit_applies(self):
        preview = {"rows": [_row(i, error="x") for i in range(2, 12)]}
        hints = suggest_import_fixes(preview, max_items=1)
        self.assertEqual(len(hints), 1)

    def test_empty_and_none_quiet(self):
        self.assertEqual(suggest_import_fixes({"rows": []}), [])
        self.assertEqual(suggest_import_fixes(None), [])
        self.assertEqual(suggest_import_fixes({}), [])


if __name__ == "__main__":
    unittest.main()
