import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_duplicate_detection_only_compares_blocked_candidates(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = ContactManagement(Path(temp))
            contacts = [
                {"contact_id": str(index), "fields": {"display_name": f"Person {index}", "email": f"person-{index}@example.test"}}
                for index in range(120)
            ]
            calls = 0
            original = manager._duplicate_score

            def counted(left, right):
                nonlocal calls
                calls += 1
                return original(left, right)

            manager._duplicate_score = counted
            with patch.object(manager.store, "contacts", return_value=contacts):
                self.assertEqual([], manager.duplicate_candidates("admin"))
            self.assertEqual(0, calls)

    def test_duplicate_candidates_recommend_richer_contact_and_cap_bulk_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = ContactManagement(Path(temp))
            contacts = []
            for index in range(101):
                email = f"person-{index}@example.test"
                contacts.extend((
                    {"contact_id": f"thin-{index}", "fields": {"display_name": f"Person {index}", "email": email}},
                    {"contact_id": f"rich-{index}", "fields": {"display_name": f"Person {index} Import", "email": email, "phone": f"123456{index}"}},
                ))

            with patch.object(manager.store, "contacts", return_value=contacts):
                candidates = manager.duplicate_candidates("admin")

            self.assertEqual(101, len(candidates))
            self.assertEqual(100, sum(candidate.bulk_eligible for candidate in candidates))
            self.assertTrue(all(candidate.left["contact_id"].startswith("rich-") for candidate in candidates))

    def test_duplicate_preview_separates_additions_and_conflicts(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = ContactManagement(Path(temp))
            left = manager.store.upsert({"display_name": "Amy Beispiel", "email": "amy@example.test", "company": ""}, "admin")
            right = manager.store.upsert({"display_name": "Amy Beispiel", "email": "other@example.test", "company": "Muster GmbH"}, "admin")

            differences = manager.merge_preview(left, right)

            by_field = {row["field"]: row for row in differences}
            self.assertEqual("addition", by_field["company"]["kind"])
            self.assertEqual("conflict", by_field["email"]["kind"])
            self.assertNotIn("display_name", by_field)

    def test_merge_preserves_target_values_and_fills_missing_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ContactStore(root)
            target = store.upsert({"display_name": "Amy Beispiel", "email": "amy@example.test", "company": ""}, "admin")
            source = store.upsert({"display_name": "Amy B.", "email": "other@example.test", "company": "Muster GmbH", "phone": "12345"}, "admin", source={"provider": "carddav", "source_id": "remote-1"})
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
            self.assertEqual("carddav", merged["merged_from"][-1]["source"]["provider"])
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

    def test_bulk_merge_combines_independent_safe_pairs_in_one_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ContactStore(root)
            first = store.upsert({"display_name": "Amy", "email": "amy@example.test", "company": "Example GmbH"}, "admin")
            first_duplicate = store.upsert({"display_name": "Amy B.", "email": "AMY@example.test", "phone": "123456"}, "admin")
            second = store.upsert({"display_name": "Ben", "email": "ben@example.test"}, "admin")
            second_duplicate = store.upsert({"display_name": "Benjamin", "email": "BEN@example.test", "website": "https://example.test"}, "admin")

            merged = ContactManagement(root).bulk_merge([
                (first["contact_id"], first_duplicate["contact_id"]),
                (second_duplicate["contact_id"], second["contact_id"]),
            ], "admin")

            self.assertEqual(2, len(merged))
            self.assertEqual(2, len(store.contacts("admin")))
            by_id = {row["contact_id"]: row for row in merged}
            self.assertEqual("123456", by_id[first["contact_id"]]["fields"]["phone"])
            self.assertEqual("https://example.test", by_id[second_duplicate["contact_id"]]["fields"]["website"])
            snapshots = ContactManagement(root)._read_snapshots()["snapshots"]
            self.assertEqual(4, len([row for row in snapshots if row["reason"].startswith("merge_")]))

    def test_bulk_merge_rejects_overlapping_pairs_without_changing_contacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ContactStore(root)
            first = store.upsert({"display_name": "Amy", "email": "amy@example.test"}, "admin")
            second = store.upsert({"display_name": "Amy B.", "email": "amy@example.test"}, "admin")
            third = store.upsert({"display_name": "Amy C.", "email": "amy@example.test"}, "admin")

            with self.assertRaisesRegex(ValueError, "only occur in one"):
                ContactManagement(root).bulk_merge([
                    (first["contact_id"], second["contact_id"]),
                    (first["contact_id"], third["contact_id"]),
                ], "admin")

            self.assertEqual(3, len(store.contacts("admin")))
            self.assertEqual([], ContactManagement(root)._read_snapshots()["snapshots"])

    def test_bulk_merge_rejects_field_conflicts_without_partial_merge(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ContactStore(root)
            safe_left = store.upsert({"display_name": "Amy", "email": "amy@example.test"}, "admin")
            safe_right = store.upsert({"display_name": "Amy B.", "email": "amy@example.test"}, "admin")
            conflict_left = store.upsert({"display_name": "Ben", "email": "shared@example.test", "company": "Links GmbH"}, "admin")
            conflict_right = store.upsert({"display_name": "Benjamin", "email": "shared@example.test", "company": "Rechts GmbH"}, "admin")

            with self.assertRaisesRegex(ValueError, "without conflicting"):
                ContactManagement(root).bulk_merge([
                    (safe_left["contact_id"], safe_right["contact_id"]),
                    (conflict_left["contact_id"], conflict_right["contact_id"]),
                ], "admin")

            self.assertEqual(4, len(store.contacts("admin")))
            self.assertEqual([], ContactManagement(root)._read_snapshots()["snapshots"])

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
