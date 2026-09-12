import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FormWizardUiTest(unittest.TestCase):
    def test_form_overview_uses_wizard_instead_of_raw_json_editor(self):
        template = (ROOT / "templates" / "documents" / "forms.html").read_text(encoding="utf-8")
        self.assertIn("Formular-Assistent", template)
        self.assertIn('id="form-builder-form"', template)
        self.assertIn('id="add-form-field"', template)
        self.assertIn('data-wizard-step="1"', template)
        self.assertIn('data-wizard-step="2"', template)
        self.assertIn('data-wizard-step="3"', template)
        self.assertIn("Daten erfassen", template)
        self.assertIn("Auswerten", template)
        self.assertNotIn("Neue Eingabemaske oder Formularvorlage", template)

    def test_builder_supports_defaults_autocomplete_and_guidance(self):
        script = (ROOT / "static" / "js" / "form_builder.js").read_text(encoding="utf-8")
        self.assertIn("__default__:", script)
        self.assertIn("__placeholder__:", script)
        self.assertIn("__help__:", script)
        self.assertIn("Autocomplete", (ROOT / "templates" / "documents" / "forms.html").read_text(encoding="utf-8"))

    def test_record_page_has_analysis_csv_and_print(self):
        template = (ROOT / "templates" / "documents" / "form_records.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "js" / "form_records.js").read_text(encoding="utf-8")
        self.assertIn('id="analysis"', template)
        self.assertIn('id="form-export-csv"', template)
        self.assertIn('id="form-print"', template)
        self.assertIn("Summe", script)
        self.assertIn("Verteilung", script)
        self.assertIn("text/csv", script)
        self.assertIn("window.print", script)

    def test_csv_export_guards_spreadsheet_formulas(self):
        script = (ROOT / "static" / "js" / "form_records.js").read_text(encoding="utf-8")
        self.assertIn("/^[=+\\-@]/", script)


if __name__ == "__main__":
    unittest.main()
