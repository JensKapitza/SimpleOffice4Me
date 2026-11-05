"""Tests for local printer setup hints (read-only proposals)."""

import unittest

from app.printer_setup_hints import suggest_printer_review


def _printer(label, default=False, shared=False):
    return {"label": label, "is_default": default, "federation_shared": shared}


class SuggestPrinterReviewTests(unittest.TestCase):
    def test_empty_inventory_flagged(self):
        hints = suggest_printer_review([], {})
        self.assertEqual(len(hints), 1)
        self.assertIn("Keine Drucker", hints[0]["reason"])

    def test_missing_default_flagged(self):
        printers = [_printer("Büro"), _printer("Flur")]
        hints = suggest_printer_review(printers, {"federation_enabled": False})
        self.assertEqual(len(hints), 1)
        self.assertIn("Standarddrucker", hints[0]["reason"])

    def test_healthy_quiet(self):
        printers = [_printer("Büro", default=True)]
        self.assertEqual(
            suggest_printer_review(printers, {"federation_enabled": False}), []
        )

    def test_federation_mismatch_flagged(self):
        printers = [_printer("Büro", default=True)]
        hints = suggest_printer_review(printers, {"federation_enabled": True})
        self.assertEqual(len(hints), 1)
        self.assertIn("Federation", hints[0]["reason"])

    def test_shared_printer_quiet(self):
        printers = [_printer("Büro", default=True, shared=True)]
        self.assertEqual(
            suggest_printer_review(printers, {"federation_enabled": True}), []
        )

    def test_none_no_hint(self):
        self.assertEqual(suggest_printer_review(None, None), [])


if __name__ == "__main__":
    unittest.main()
