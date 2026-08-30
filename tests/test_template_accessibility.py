import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
TAG = re.compile(r"<(?P<name>main|button|th|input|form)\b[^>]*>", re.IGNORECASE)


def _attributes(tag):
    return {
        name.lower(): value
        for name, value in re.findall(r"([:\w-]+)=[\"']([^\"']*)[\"']", tag)
    }


class TemplateAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.templates = {
            path: path.read_text(encoding="utf-8") for path in TEMPLATES.rglob("*.html")
        }

    def test_main_regions_are_skip_link_targets(self):
        regions = []
        for path, content in self.templates.items():
            for match in re.finditer(r"<main\b[^>]*>", content, re.IGNORECASE):
                regions.append((path, match.group()))
        self.assertGreaterEqual(len(regions), 60)
        for path, tag in regions:
            self.assertEqual("main-content", _attributes(tag).get("id"), path)

    def test_buttons_always_declare_their_behavior(self):
        buttons = []
        for path, content in self.templates.items():
            for match in re.finditer(r"<button\b[^>]*>", content, re.IGNORECASE):
                buttons.append((path, match.group()))
        self.assertGreaterEqual(len(buttons), 200)
        for path, tag in buttons:
            self.assertIn(_attributes(tag).get("type"), {"button", "submit", "reset"}, path)

    def test_table_headers_declare_column_or_row_scope(self):
        headers = []
        for path, content in self.templates.items():
            for section, expected in (("thead", "col"), ("tbody", "row")):
                for body in re.findall(rf"<{section}\b[^>]*>(.*?)</{section}>", content, re.S | re.I):
                    for tag in re.findall(r"<th\b[^>]*>", body, re.I):
                        headers.append((path, tag, expected))
        self.assertGreaterEqual(len(headers), 170)
        for path, tag, expected in headers:
            self.assertEqual(expected, _attributes(tag).get("scope"), path)

    def test_informational_alerts_are_announced(self):
        alerts = []
        for path, content in self.templates.items():
            for tag in re.findall(r"<[^>]+class=[\"'][^\"']*\balert-info\b[^\"']*[\"'][^>]*>", content, re.I):
                alerts.append((path, tag))
        self.assertGreaterEqual(len(alerts), 40)
        for path, tag in alerts:
            attributes = _attributes(tag)
            self.assertEqual("status", attributes.get("role"), path)
            self.assertEqual("polite", attributes.get("aria-live"), path)

    def test_address_autocomplete_disables_browser_autofill(self):
        fields = []
        for path, content in self.templates.items():
            for tag in re.findall(
                r"<input\b[^>]*\b(?:data-address-field|data-address-(?:city|postal|street|state|country))[^>]*>",
                content,
                re.I,
            ):
                fields.append((path, tag))
        self.assertGreaterEqual(len(fields), 10)
        for path, tag in fields:
            self.assertEqual("off", _attributes(tag).get("autocomplete"), path)

    def test_search_landmarks_are_real_search_forms(self):
        search_forms = []
        for path, content in self.templates.items():
            for form in re.findall(r"<form\b[^>]*\brole=[\"']search[\"'][^>]*>.*?</form>", content, re.S | re.I):
                search_forms.append((path, form))
        self.assertGreaterEqual(len(search_forms), 10)
        for path, form in search_forms:
            opening = form.split(">", 1)[0]
            self.assertNotRegex(opening, r"\bmethod=[\"']post[\"']")
            query_fields = re.findall(r"<input\b[^>]*\bname=[\"']q[\"'][^>]*>", form, re.I)
            self.assertTrue(query_fields, path)
            self.assertTrue(any(_attributes(tag).get("type") == "search" for tag in query_fields), path)

    def test_collapse_controls_expose_state_and_target(self):
        controls = []
        for path, content in self.templates.items():
            for tag in re.findall(r"<button\b[^>]*\bdata-bs-toggle=[\"']collapse[\"'][^>]*>", content, re.I):
                controls.append((path, tag))
        self.assertGreaterEqual(len(controls), 5)
        for path, tag in controls:
            attributes = _attributes(tag)
            self.assertEqual("button", attributes.get("type"), path)
            self.assertIn(attributes.get("aria-expanded"), {"true", "false"}, path)
            self.assertTrue(attributes.get("aria-controls"), path)


if __name__ == "__main__":
    unittest.main()
