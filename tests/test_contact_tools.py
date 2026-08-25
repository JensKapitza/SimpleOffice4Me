import tempfile
import unittest
from pathlib import Path

from app.contact_management import ContactManagement
from app.contact_store import ContactStore
from app.contact_tools import ContactTools


class ContactToolsTest(unittest.TestCase):
    def test_bulk_export_is_permission_scoped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ContactStore(root)
            own = store.upsert({"display_name": "Own"}, "admin")
            foreign = store.upsert({"display_name": "Foreign"}, "other")
            tools = ContactTools(root)

            payload = tools.export_selected([own["contact_id"]], "admin")
            self.assertIn("FN:Own", payload)
            with self.assertRaisesRegex(ValueError, "not visible"):
                tools.export_selected([foreign["contact_id"]], "admin")

    def test_csv_import_is_all_or_nothing_for_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tools = ContactTools(root)
            csv_text = "display_name;email\nAmy;amy@example.test\n;invalid@example.test\n"

            with self.assertRaisesRegex(ValueError, "nothing was imported"):
                tools.import_csv(csv_text, "admin")

            self.assertEqual([], ContactStore(root).contacts("admin"))

    def test_csv_import_skips_existing_email_and_creates_new_contacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ContactStore(root)
            store.upsert({"display_name": "Existing", "email": "same@example.test"}, "admin")
            tools = ContactTools(root)
            csv_text = "display_name;email;company\nDuplicate;same@example.test;A\nNew;new@example.test;B\n"

            result = tools.import_csv(csv_text, "admin")

            self.assertEqual(1, result["created"])
            self.assertEqual(1, result["skipped_duplicates"])
            self.assertEqual(2, len(store.contacts("admin")))
            created = next(item for item in store.contacts("admin") if item["fields"].get("email") == "new@example.test")
            self.assertEqual("csv_import", created["source"]["provider"])

    def test_preview_marks_email_duplicates_without_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ContactStore(root)
            store.upsert({"display_name": "Existing", "email": "same@example.test"}, "admin")
            tools = ContactTools(root)

            preview = tools.preview_csv("display_name,email\nDuplicate,same@example.test\nNew,new@example.test\n", "admin")

            self.assertEqual(2, preview["valid"])
            self.assertEqual(1, preview["duplicates"])
            self.assertEqual(1, len(store.contacts("admin")))

    def test_snapshot_compare_reports_field_and_metadata_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ContactStore(root)
            contact = store.upsert({"display_name": "Amy", "email": "old@example.test"}, "admin")
            manager = ContactManagement(root)
            manager.update_metadata(contact["contact_id"], "admin", ["Alt"], ["Nord"])
            snapshot = manager.snapshots(contact["contact_id"], "admin")[0]
            store.upsert({"display_name": "Amy", "email": "new@example.test"}, "admin", contact["contact_id"])
            manager.update_metadata(contact["contact_id"], "admin", ["Neu"], ["Nord"])

            comparison = ContactTools(root).compare_snapshot(contact["contact_id"], snapshot["snapshot_id"], "admin")

            self.assertGreaterEqual(comparison["changed_count"], 2)
            email = next(row for row in comparison["fields"] if row["field"] == "email")
            self.assertTrue(email["changed"])
            tags = next(row for row in comparison["metadata"] if row["field"] == "tags")
            self.assertTrue(tags["changed"])


if __name__ == "__main__":
    unittest.main()
