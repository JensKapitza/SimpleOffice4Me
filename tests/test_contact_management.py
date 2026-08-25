import tempfile
import unittest
from pathlib import Path

from app.contact_management import ContactManagement
from app.contact_store import ContactStore


class ContactManagementTest(unittest.TestCase):
    def test_duplicate_detection_uses_normalized_email_and_phone(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            store.upsert({"display_name": "Amy Beispiel", "email": "Amy@Example.Test", "phone": "+49 170 123456"}, "admin")
            store.upsert({"display_name": "Amy B.", "email": "amy@example.test", "phone": "+49 (170) 123456"}, "admin")

            pairs = ContactManagement(Path(temp)).duplicate_candidates("admin")

            self.assertEqual(1, len(pairs))
            self.assertEqual(100, pairs[0].score)
            self.assertIn("gleiche E-Mail", pairs[0].reasons)
            self.assertIn("gleiche Telefonnummer", pairs[0].reasons)

    def test_merge_preserves_target_values_and_fills_missing_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ContactStore(root)
            target = store.upsert({"display_name": "Amy Beispiel", "email": "amy@example.test", "company": ""}, "admin")
            source = store.upsert({"display_name": "Amy B.", "email": "other@example.test", "company": "Muster GmbH", "phone": "12345"}, "admin")
            store.add_address(source["contact_id"], "Büro", "Musterweg 1", "admin")
            manager = ContactManagement(root)
            manager.update_metadata(target["contact_id"], "admin", ["Kunde"], ["A"])
            manager.update_metadata(source["contact_id"], "admin", ["VIP"], ["B"])

            merged = manager.merge(target["contact_id"], source["contact_id"], "admin")

            self.assertEqual("amy@example.test", merged["fields"]["email"])
            self.assertEqual("Muster GmbH", merged["fields"]["company"])
            self.assertEqual("12345", merged["fields"]["phone"])
            self.assertEqual(["Kunde", "VIP"], merged["tags"])
            self.assertEqual(["A", "B"], merged["groups"])
            self.assertEqual(1, len(merged["addresses"]))
            self.assertEqual(1, len(store.contacts("admin")))
            reasons = {row["reason"] for row in manager._read_snapshots()["snapshots"]}
            self.assertIn("merge_target", reasons)
            self.assertIn("merge_source", reasons)

    def test_merge_rejects_contacts_with_different_owners(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ContactStore(root)
            left = store.upsert({"display_name": "Links"}, "admin")
            right = store.upsert({"display_name": "Rechts"}, "other")
            store.share(left["contact_id"], ["other"], "admin")
            store.share(right["contact_id"], ["admin"], "other")

            with self.assertRaisesRegex(ValueError, "different owners"):
                ContactManagement(root).merge(left["contact_id"], right["contact_id"], "admin")

    def test_bulk_metadata_is_atomic_for_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ContactStore(root)
            editable = store.upsert({"display_name": "Bearbeitbar"}, "admin")
            foreign = store.upsert({"display_name": "Fremd"}, "other")
            manager = ContactManagement(root)

            with self.assertRaisesRegex(ValueError, "not editable"):
                manager.bulk_metadata([editable["contact_id"], foreign["contact_id"]], "admin", ["Test"], [])

            self.assertEqual([], store.get(editable["contact_id"], "admin").get("tags", []))

    def test_snapshot_restore_restores_full_contact_and_preserves_current_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ContactStore(root)
            contact = store.upsert({"display_name": "Ruby", "email": "old@example.test"}, "admin")
            manager = ContactManagement(root)
            manager.update_metadata(contact["contact_id"], "admin", ["Alt"], ["Familie"])
            first_snapshot = manager.snapshots(contact["contact_id"], "admin")[0]
            store.upsert({"display_name": "Ruby", "email": "new@example.test"}, "admin", contact["contact_id"])

            restored = manager.restore(first_snapshot["snapshot_id"], "admin")

            self.assertEqual("old@example.test", restored["fields"]["email"])
            self.assertGreaterEqual(len(manager.snapshots(contact["contact_id"], "admin")), 2)

    def test_advanced_search_filters_tags_groups_and_quality(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ContactStore(root)
            one = store.upsert({"display_name": "Kontakt Eins", "company": "Firma A"}, "admin")
            two = store.upsert({"display_name": "Kontakt Zwei", "email": "two@example.test", "phone": "123456", "company": "Firma B"}, "admin")
            manager = ContactManagement(root)
            manager.update_metadata(one["contact_id"], "admin", ["Kunde"], ["Nord"])
            manager.update_metadata(two["contact_id"], "admin", ["Lieferant"], ["Süd"])

            self.assertEqual([one["contact_id"]], [item["contact_id"] for item in manager.advanced_search("admin", tag="Kunde")])
            self.assertEqual([one["contact_id"]], [item["contact_id"] for item in manager.advanced_search("admin", incomplete="email")])
            dashboard = manager.dashboard("admin")
            self.assertEqual(2, dashboard["total"])
            self.assertEqual(1, dashboard["missing_email"])
            self.assertIn("Kunde", dashboard["tags"])


if __name__ == "__main__":
    unittest.main()
