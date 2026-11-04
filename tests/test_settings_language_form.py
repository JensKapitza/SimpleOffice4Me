"""Regression checks for the settings session-language form."""

import unittest
from pathlib import Path


TEMPLATE = Path(__file__).resolve().parents[1] / "templates/documents/settings.html"


class SettingsLanguageFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = TEMPLATE.read_text(encoding="utf-8")

    def test_language_form_is_separate_from_general_settings_form(self):
        language = '<form id="settings-session-language-form"'
        general = '<form method="post" class="row g-4"'
        self.assertEqual(1, self.source.count(language))
        self.assertEqual(1, self.source.count(general))
        language_start = self.source.index(language)
        general_start = self.source.index(general)
        self.assertLess(language_start, general_start)
        self.assertIn("</form>", self.source[language_start:general_start])
        self.assertIn("csrf_token()", self.source[language_start:general_start])

    def test_language_controls_submit_only_the_language_form(self):
        select_start = self.source.index('<select class="form-select" id="session-language"')
        select_end = self.source.index(">", select_start)
        self.assertIn('form="settings-session-language-form"', self.source[select_start:select_end])
        self.assertIn('form="settings-session-language-form"', self.source)
        self.assertIn('action="{{ url_for(\'documents.set_language\') }}"', self.source)

    def test_settings_navigation_has_english_and_german_labels(self):
        for label in ("General", "Allgemein", "Local folders", "Lokale Ordner"):
            with self.subTest(label=label):
                self.assertIn(label, self.source)


    def test_settings_sections_render_language_specific_labels(self):
        expected = (
            ("Documents and import", "Dokumente und Import"),
            ("Status for web uploads", "Zustand für Web-Uploads"),
            ("Additional default tags", "Zusätzliche Standard-Tags"),
            ("Default visibility", "Standardsichtbarkeit"),
            ("Default duration in minutes", "Standarddauer in Minuten"),
            ("Sharing links", "Freigaben"),
            ("WebDAV for desktop applications", "WebDAV für Desktop-Programme"),
        )
        for english, german in expected:
            with self.subTest(label=german):
                self.assertIn(
                    f"'{english}' if g.language == 'en' else '{german}'",
                    self.source,
                )
                self.assertNotIn(f">{german}<", self.source)


if __name__ == "__main__":
    unittest.main()
