import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "browser"))
from layout_checks import check_readable_layout


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Browser regression requires Playwright")
class BrowserLayoutTests(unittest.TestCase):
    def test_collapsed_text_is_detected_and_responsive_grid_passes(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).is_file():
                self.skipTest("Chromium is not installed")
            browser = playwright.chromium.launch(args=["--no-sandbox"])
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.set_content('<main style="display:flex;width:800px"><div style="flex:1;min-width:0"><h2>Netzwerk scannen und Geräte verbinden</h2></div><form style="width:799px;flex-shrink:0"></form></main>')
                checks = check_readable_layout(page)
                self.assertTrue(all(row["collapsed_text"] for row in checks))
                self.assertEqual(1440, page.viewport_size["width"])
                page.set_content('<main style="display:grid;grid-template-columns:minmax(0,1fr);max-width:100%"><h2>Netzwerk scannen und Geräte verbinden</h2></main>')
                self.assertTrue(all(not row["collapsed_text"] for row in check_readable_layout(page)))
            finally:
                browser.close()
