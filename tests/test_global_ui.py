from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GlobalUiQuickWinsTest(unittest.TestCase):
    def test_layout_loads_accessibility_helpers_after_translations(self):
        layout = (ROOT / "templates" / "layout.html").read_text(encoding="utf-8")
        self.assertIn('class="skip-link" href="#main-content"', layout)
        self.assertLess(layout.index("js/i18n.js"), layout.index("js/global_ui.js"))

    def test_helper_has_keyboard_and_repeat_submit_guards(self):
        script = (ROOT / "static" / "js" / "global_ui.js").read_text(encoding="utf-8")
        self.assertIn('event.key !== "/"', script)
        self.assertIn('input.type = "search"', script)
        self.assertIn('setAttribute("role", "search")', script)
        self.assertIn('form.dataset.allowMultipleSubmit', script)
        self.assertIn('setAttribute("aria-busy", "true")', script)
        self.assertIn('window.addEventListener("pageshow"', script)
        self.assertIn('rel.add("noopener")', script)


if __name__ == "__main__":
    unittest.main()
