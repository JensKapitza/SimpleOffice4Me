"""Tests for local screen sharing hints (read-only proposals)."""

import unittest

from app.screen_ready_hints import suggest_screen_review


class SuggestScreenReviewTests(unittest.TestCase):
    def test_windows_missing_flagged(self):
        status = {"platform": "windows", "windows": {"wireless_display": "not-installed"}}
        hints = suggest_screen_review(status)
        self.assertEqual(len(hints), 1)
        self.assertIn("Drahtlose Anzeige", hints[0]["reason"])

    def test_windows_ready_quiet(self):
        status = {"platform": "windows", "windows": {"wireless_display": "installed"}}
        self.assertEqual(suggest_screen_review(status), [])

    def test_linux_no_tools_flagged(self):
        status = {"platform": "linux", "linux": {}}
        hints = suggest_screen_review(status)
        self.assertEqual(len(hints), 1)
        self.assertIn("Browser-Capture", hints[0]["reason"])

    def test_linux_with_tool_quiet(self):
        status = {"platform": "linux", "linux": {"gnome_network_displays": True}}
        self.assertEqual(suggest_screen_review(status), [])

    def test_unknown_platform_quiet(self):
        status = {"platform": "android", "windows": {}, "linux": {}}
        self.assertEqual(suggest_screen_review(status), [])

    def test_empty_no_hint(self):
        self.assertEqual(suggest_screen_review(None), [])
        self.assertEqual(suggest_screen_review({}), [])


if __name__ == "__main__":
    unittest.main()
