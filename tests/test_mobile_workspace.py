"""Mobile workspace: quick-access bar and contextual action button markers."""
from __future__ import annotations

import unittest
from pathlib import Path

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:  # `unittest discover -s tests` puts tests/ on sys.path
    from http_api_fixture import PublicHttpTestCase


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "static" / "js" / "mobile_workspace.js"
STYLES = ROOT / "static" / "css" / "mobile-workspace.css"


class MobileWorkspaceTests(PublicHttpTestCase):
    def test_layout_embeds_mobile_bar_fab_and_assets(self):
        html = self.client.get("/documents/").get_data(as_text=True)
        for marker in (
            "data-mobile-bar", "data-mobile-fab", "data-mobile-fab-menu",
            "mobile-workspace.css", "mobile_workspace.js",
        ):
            self.assertIn(marker, html)
        for href in ("/documents/quick-search", "/documents/calendar", "/documents/",
                     "/tasks/", "/chat/"):
            self.assertIn(f'href="{href}"', html)

    def test_fab_lists_contextual_actions(self):
        html = self.client.get("/documents/").get_data(as_text=True)
        for action in ("event", "capture", "task", "share"):
            self.assertIn(f'data-mobile-action="{action}"', html)

    def test_workspace_script_stays_safe(self):
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", script)
        self.assertIn("navigator.share", script)
        self.assertIn("clipboard", script)
        self.assertIn("aria-current", script)

    def test_workspace_styles_keep_desktop_clean_and_targets_large(self):
        css = STYLES.read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 991.98px)", css)
        self.assertIn("min-height: 64px", css)
        self.assertIn("width: 56px", css)
        self.assertIn("min-height: 48px", css)
        self.assertIn("safe-area-inset-bottom", css)
        self.assertIn('html[data-so-keyboard-open="1"] .so-fab-wrap', css)


if __name__ == "__main__":
    unittest.main()
