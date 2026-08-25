import tempfile
import unittest
from pathlib import Path

from app.contact_management import ContactManagement


class ContactDuplicateSolverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.manager = ContactManagement(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def _contact(self, name, email="", phone="", owner="alice"):
        return self.manager.store.upsert(
            {"display_name": name, "email": email, "phone": phone},
            owner,
        )

    def test_whitespace_and_case_are_trivial_name_differences(self):
        left = self._contact("  Max   Mustermann ", "MAX@example.test")
        right = self._contact("max mustermann", "max@example.test")
        score, reasons, trivial = self.manager._duplicate_score(left, right)
        self.assertGreaterEqual(score, 90)
        self.assertTrue(trivial)
        self.assertIn("gleiche E-Mail", reasons)
        self.assertIn("gleicher Anzeigename", reasons)

    def test_address_spacing_and_street_spelling_match(self):
        left = self._contact("Erika Muster")
        right = self._contact("Erika Muster")
        self.manager.store.add_address(left["contact_id"], "Privat", "Musterstraße  12, 12345 Berlin", "alice")
        self.manager.store.add_address(right["contact_id"], "privat", "Musterstr. 12 12345 Berlin", "alice")
        left = self.manager.store.get(left["contact_id"], "alice")
        right = self.manager.store.get(right["contact_id"], "alice")
        score, reasons, trivial = self.manager._duplicate_score(left, right)
        self.assertGreaterEqual(score, 80)
        self.assertTrue(trivial)
        self.assertIn("gleiche Adresse normalisiert", reasons)

    def test_merge_deduplicates_normalized_addresses_and_preserves_acl(self):
        left = self._contact("Test Person", "person@example.test")
        right = self._contact(" Test   Person ", "PERSON@example.test")
        self.manager.store.add_address(left["contact_id"], "Privat", "Teststraße 1, 40210 Düsseldorf", "alice")
        self.manager.store.add_address(right["contact_id"], "privat", "Teststr. 1 40210 Düsseldorf", "alice")
        self.manager.store.share(left["contact_id"], [], "alice", ["reader-a"])
        self.manager.store.share(right["contact_id"], ["editor-a"], "alice", ["reader-b"])

        merged = self.manager.merge(left["contact_id"], right["contact_id"], "alice")
        self.assertEqual("Test Person", merged["fields"]["display_name"])
        self.assertEqual("person@example.test", merged["fields"]["email"])
        self.assertEqual(1, len(merged["addresses"]))
        self.assertIn("editor-a", merged["managers"])
        self.assertIn("reader-a", merged["readers"])
        self.assertIn("reader-b", merged["readers"])

    def test_conflicting_core_fields_reduce_confidence(self):
        left = self._contact("Alex Beispiel", "one@example.test", "+49 211 123456")
        right = self._contact("Alex Beispiel", "two@example.test", "+49 211 999999")
        score, reasons, trivial = self.manager._duplicate_score(left, right)
        self.assertFalse(trivial)
        self.assertIn("2 abweichende Kernfelder", reasons)
        self.assertLess(score, 70)


if __name__ == "__main__":
    unittest.main()
