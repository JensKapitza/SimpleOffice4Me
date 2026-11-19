"""Quick-search workspace page: live search markers, preview pane and collections."""
from __future__ import annotations

import unittest
from pathlib import Path

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:  # `unittest discover -s tests` puts tests/ on sys.path
    from http_api_fixture import PublicHttpTestCase


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "static" / "js" / "quick_search_workspace.js"
STYLES = ROOT / "static" / "css" / "quick_search.css"


class QuickSearchWorkspaceTests(PublicHttpTestCase):
    def test_workspace_shell_exposes_api_preview_and_collections(self):
        html = self.client.get("/documents/quick-search").get_data(as_text=True)
        for marker in (
            "data-quick-workspace", "data-quick-api=", "data-quick-preview=",
            "data-quick-list", "data-quick-preview", "data-quick-recents",
            "data-quick-favorites", "quick_search_workspace.js", "css/quick_search.css",
        ):
            self.assertIn(marker, html)

    def test_all_type_filters_exist_including_new_kinds(self):
        html = self.client.get("/documents/quick-search?q=test").get_data(as_text=True)
        for kind in ("all", "document", "appointment", "task", "chat", "contact",
                     "project", "object", "invoice", "link"):
            self.assertIn(f'data-quick-filter="{kind}"', html)

    def test_workspace_script_stays_safe_and_abortable(self):
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", script)
        self.assertIn("textContent", script)
        self.assertIn("AbortController", script)
        self.assertIn("setTimeout", script)
        self.assertIn("quicksearch.recent.v1", script)
        self.assertIn("quicksearch.fav.v1", script)

    def test_workspace_styles_keep_touch_targets_and_active_state(self):
        css = STYLES.read_text(encoding="utf-8")
        self.assertIn(".quick-card-active", css)
        self.assertIn("min-height:44px", css.replace(" ", ""))
        self.assertIn("position:sticky", css.replace(" ", ""))


if __name__ == "__main__":
    unittest.main()
