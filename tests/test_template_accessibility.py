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
            self.assertTrue(_attributes(opening).get("aria-label"), path)
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

    def test_images_declare_loading_decoding_and_alternative_text(self):
        images = []
        for path, content in self.templates.items():
            images.extend((path, tag) for tag in re.findall(r"<img\b[^>]*>", content, re.I))
        self.assertGreaterEqual(len(images), 4)
        for path, tag in images:
            attributes = _attributes(tag)
            self.assertIn("alt", attributes, path)
            self.assertTrue(attributes.get("loading"), path)
            self.assertEqual("async", attributes.get("decoding"), path)
        eager = [(path, tag) for path, tag in images if "eager" in _attributes(tag).get("loading", "")]
        self.assertTrue(eager)
        for path, tag in eager:
            self.assertIn("high", _attributes(tag).get("fetchpriority", ""), path)

    def test_new_windows_do_not_send_referrers(self):
        links = []
        for path, content in self.templates.items():
            links.extend((path, tag) for tag in re.findall(r"<a\b[^>]*\btarget=[\"']_blank[\"'][^>]*>", content, re.I))
        self.assertGreaterEqual(len(links), 7)
        for path, tag in links:
            relations = set(_attributes(tag).get("rel", "").split())
            self.assertTrue({"noopener", "noreferrer"}.issubset(relations), path)

    def test_forms_and_navigation_declare_browser_semantics(self):
        forms, navigation = [], []
        for path, content in self.templates.items():
            forms.extend((path, tag) for tag in re.findall(r"<form\b[^>]*>", content, re.I))
            navigation.extend((path, tag) for tag in re.findall(r"<nav\b[^>]*>", content, re.I))
        self.assertGreaterEqual(len(forms), 180)
        self.assertGreaterEqual(len(navigation), 10)
        for path, tag in forms:
            self.assertIn(_attributes(tag).get("method"), {"get", "post"}, path)
        for path, tag in navigation:
            attributes = _attributes(tag)
            self.assertTrue(attributes.get("aria-label") or attributes.get("aria-labelledby"), path)

    def test_paging_and_dialog_controls_expose_their_purpose(self):
        paging, close_buttons = [], []
        for path, content in self.templates.items():
            for tag in re.findall(r"<a\b[^>]*>", content, re.I):
                if re.search(r"\b(?:event_)?page\s*=\s*(?:event_)?page\s*[+-]\s*1\b", tag):
                    paging.append((path, tag))
            for tag in re.findall(r"<button\b[^>]*>", content, re.I):
                if "btn-close" in _attributes(tag).get("class", "").split():
                    close_buttons.append((path, tag))
        self.assertGreaterEqual(len(paging), 16)
        self.assertGreaterEqual(len(close_buttons), 2)
        for path, tag in paging:
            expected = "prev" if re.search(r"-\s*1\b", tag) else "next"
            self.assertIn(expected, _attributes(tag).get("rel", "").split(), path)
        for path, tag in close_buttons:
            self.assertTrue(_attributes(tag).get("aria-label"), path)

    def test_personnel_tables_and_actions_support_small_screens(self):
        personnel = [
            self.templates[TEMPLATES / "personnel" / name]
            for name in ("index.html", "hr.html", "team_calendar.html")
        ]
        for content in personnel:
            self.assertIn("personnel-page", content)
            self.assertIn("personnel-mobile-table", content)
            self.assertIn("data-label=", content)
        self.assertIn("personnel-punch-actions", personnel[0])
        self.assertIn("personnel-row-actions", personnel[0])
        self.assertIn("personnel-row-actions", personnel[1])


if __name__ == "__main__":
    unittest.main()
