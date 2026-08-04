import unittest
from pathlib import Path

from app.settings_store import UI_LITERAL_TRANSLATIONS


ROOT = Path(__file__).resolve().parents[1]


class ContactHistoryUiTests(unittest.TestCase):
    def test_contact_history_has_user_and_field_filters(self):
        template = (ROOT / "templates" / "documents" / "contact_detail.html").read_text(encoding="utf-8")

        self.assertIn("data-contact-history-actor-filter", template)
        self.assertIn("data-contact-history-field-filter", template)
        self.assertIn('data-contact-history-actor="{{ change.actor }}"', template)
        self.assertIn('data-contact-history-field="{{ change.field }}"', template)
        self.assertIn("js/contact-history.js", template)

    def test_history_script_keeps_actor_label_and_uses_safe_dom_updates(self):
        script = (ROOT / "static" / "js" / "contact-history.js").read_text(encoding="utf-8")

        self.assertIn("actorStyle(entry.dataset.contactHistoryActor", script)
        self.assertIn("entry.hidden", script)
        self.assertIn("count.textContent", script)
        self.assertNotIn("innerHTML", script)

    def test_history_filters_are_available_in_english(self):
        translations = UI_LITERAL_TRANSLATIONS["en"]

        self.assertEqual("All editors", translations["Alle Bearbeiter"])
        self.assertEqual("All changed fields", translations["Alle geänderten Felder"])
        self.assertEqual("No changes match the filter.", translations["Keine Änderungen entsprechen dem Filter."])


if __name__ == "__main__":
    unittest.main()
