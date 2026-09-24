import tempfile
import unittest
from pathlib import Path

from app.shopping_store import ShoppingStore


class ShoppingStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ShoppingStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_personal_list_and_item_roundtrip(self):
        shopping = self.store.create_list("Wocheneinkauf", "alice", list_id="weekly", store="Supermarkt")
        item = self.store.add_item("weekly", "Milch", "alice", {"quantity": "2", "unit": "l", "priority": 3})
        self.assertEqual("weekly", shopping["list_id"])
        self.assertEqual("Milch", self.store.items("alice", list_id="weekly")[0]["name"])
        self.assertEqual("2", item["quantity"])

    def test_other_user_cannot_read_or_change_private_list(self):
        self.store.create_list("Privat", "alice", list_id="private")
        item = self.store.add_item("private", "Kaffee", "alice")
        self.assertEqual([], self.store.lists("bob"))
        self.assertEqual([], self.store.items("bob"))
        with self.assertRaisesRegex(ValueError, "not found"):
            self.store.update_item(item["item_id"], "bob", {"status": "bought"})

    def test_status_keeps_unavailable_item_instead_of_deleting_it(self):
        self.store.create_list("Drogerie", "alice", list_id="drugstore")
        item = self.store.add_item("drugstore", "Produkt", "alice")
        changed = self.store.update_item(item["item_id"], "alice", {"status": "unavailable"})
        self.assertEqual("unavailable", changed["status"])
        self.assertEqual(1, len(self.store.items("alice", list_id="drugstore")))

    def test_bought_item_can_be_reactivated(self):
        self.store.create_list("Liste", "alice", list_id="list")
        item = self.store.add_item("list", "Brot", "alice")
        bought = self.store.update_item(item["item_id"], "alice", {"status": "bought"})
        self.assertTrue(bought["completed_at"])
        reopened = self.store.update_item(item["item_id"], "alice", {"status": "open"})
        self.assertEqual("", reopened["completed_at"])

    def test_archive_hides_list_without_destroying_items(self):
        self.store.create_list("Alt", "alice", list_id="old")
        self.store.add_item("old", "Salz", "alice")
        self.store.archive_list("old", "alice")
        self.assertEqual([], self.store.lists("alice"))
        self.assertEqual(1, len(self.store.lists("alice", include_archived=True)))
        self.assertEqual(1, len(self.store.items("alice", list_id="old")))

    def test_history_is_written_for_mutations(self):
        self.store.create_list("Liste", "alice", list_id="audit")
        self.store.add_item("audit", "Äpfel", "alice")
        events = list((self.root / ".simpleoffice-history" / "events").glob("*.json"))
        self.assertGreaterEqual(len(events), 2)

    def test_shared_read_access_does_not_grant_mutation(self):
        self.store.create_list("Familie", "alice", list_id="family")
        item = self.store.add_item("family", "Milch", "alice")
        self.store.share_list("family", "alice", "bob", ["read"])
        self.assertEqual(["Familie"], [row["name"] for row in self.store.lists("bob")])
        self.assertEqual(["Milch"], [row["name"] for row in self.store.items("bob", list_id="family")])
        with self.assertRaisesRegex(ValueError, "not found"):
            self.store.add_item("family", "Brot", "bob")
        with self.assertRaisesRegex(ValueError, "not found"):
            self.store.update_item(item["item_id"], "bob", {"status": "bought"})

    def test_add_permission_does_not_grant_edit_or_complete(self):
        self.store.create_list("Familie", "alice", list_id="family-add")
        self.store.share_list("family-add", "alice", "bob", ["add"])
        item = self.store.add_item("family-add", "Brot", "bob")
        self.assertEqual("bob", item["created_by"])
        with self.assertRaisesRegex(ValueError, "not found"):
            self.store.update_item(item["item_id"], "bob", {"name": "Toast"})
        with self.assertRaisesRegex(ValueError, "not found"):
            self.store.update_item(item["item_id"], "bob", {"status": "bought"})

    def test_complete_permission_can_take_and_finish_without_editing_content(self):
        self.store.create_list("Mitbringen", "alice", list_id="bring")
        item = self.store.add_item("bring", "Batterien", "alice")
        self.store.share_list("bring", "alice", "bob", ["complete"])
        taken = self.store.take_item(item["item_id"], "bob")
        self.assertEqual("taken", taken["status"])
        self.assertEqual("bob", taken["assigned_to"])
        bought = self.store.update_item(item["item_id"], "bob", {"status": "bought"})
        self.assertTrue(bought["completed_at"])
        with self.assertRaisesRegex(ValueError, "not found"):
            self.store.update_item(item["item_id"], "bob", {"note": "changed"})

    def test_manage_permission_can_share_and_revoke(self):
        self.store.create_list("Gruppe", "alice", list_id="managed")
        self.store.share_list("managed", "alice", "bob", ["manage"])
        self.store.share_list("managed", "bob", "carol", ["read"], principal_type="contact")
        self.assertEqual(1, len(self.store.lists("carol")))
        self.store.revoke_share("managed", "bob", "carol", principal_type="contact")
        self.assertEqual([], self.store.lists("carol"))

    def test_revoked_user_loses_access_immediately(self):
        self.store.create_list("Liste", "alice", list_id="revoke")
        self.store.share_list("revoke", "alice", "bob", ["read", "complete"])
        self.assertEqual(1, len(self.store.lists("bob")))
        self.store.revoke_share("revoke", "alice", "bob")
        self.assertEqual([], self.store.lists("bob"))
        with self.assertRaisesRegex(ValueError, "not found"):
            self.store.items("bob", list_id="revoke")

    def test_share_permissions_are_audited(self):
        self.store.create_list("Audit", "alice", list_id="share-audit")
        self.store.share_list("share-audit", "alice", "bob", ["read", "add"])
        self.store.revoke_share("share-audit", "alice", "bob")
        events = list((self.root / ".simpleoffice-history" / "events").glob("*.json"))
        self.assertGreaterEqual(len(events), 3)

    def test_items_by_store_uses_item_override_then_list_default(self):
        self.store.create_list("Woche", "alice", list_id="weekly-store", store="Aldi")
        self.store.add_item("weekly-store", "Milch", "alice")
        self.store.add_item("weekly-store", "Shampoo", "alice", {"store": "dm"})
        grouped = self.store.items_by_store("alice")
        self.assertEqual(["Milch"], [row["name"] for row in grouped["Aldi"]])
        self.assertEqual(["Shampoo"], [row["name"] for row in grouped["dm"]])
        self.assertEqual("Woche", grouped["Aldi"][0]["list_name"])

    def test_store_view_can_filter_across_multiple_visible_lists(self):
        self.store.create_list("Woche", "alice", list_id="weekly-filter", store="Lidl")
        self.store.create_list("Party", "alice", list_id="party-filter")
        self.store.add_item("weekly-filter", "Brot", "alice")
        self.store.add_item("party-filter", "Chips", "alice", {"store": "lidl"})
        grouped = self.store.items_by_store("alice", store="LIDL")
        self.assertEqual({"Lidl", "lidl"}, set(grouped))
        self.assertEqual(2, sum(len(rows) for rows in grouped.values()))

    def test_store_view_defaults_to_open_items_and_excludes_archived_lists(self):
        self.store.create_list("Aktiv", "alice", list_id="active-store", store="Markt")
        self.store.create_list("Alt", "alice", list_id="old-store", store="Markt")
        bought = self.store.add_item("active-store", "Schon gekauft", "alice")
        self.store.update_item(bought["item_id"], "alice", {"status": "bought"})
        self.store.add_item("active-store", "Noch offen", "alice")
        self.store.add_item("old-store", "Archiviert", "alice")
        self.store.archive_list("old-store", "alice")
        grouped = self.store.items_by_store("alice")
        self.assertEqual(["Noch offen"], [row["name"] for row in grouped["Markt"]])

    def test_shared_reader_store_view_respects_visibility(self):
        self.store.create_list("Geteilt", "alice", list_id="shared-store", store="IKEA")
        self.store.create_list("Privat", "alice", list_id="private-store", store="IKEA")
        self.store.add_item("shared-store", "Batterien", "alice")
        self.store.add_item("private-store", "Box", "alice")
        self.store.share_list("shared-store", "alice", "bob", ["read"])
        grouped = self.store.items_by_store("bob", store="ikea")
        self.assertEqual(["Batterien"], [row["name"] for rows in grouped.values() for row in rows])


    def test_product_memory_tracks_metadata_favorite_and_purchases(self):
        self.store.create_list("Woche", "alice", list_id="product-memory", store="Lidl")
        item = self.store.add_item(
            "product-memory",
            "Haferdrink",
            "alice",
            {
                "barcode": "4006381333931",
                "brand": "Testmarke",
                "pack_size": "1 l",
                "price": "1,49",
                "category": "Getränke",
            },
        )
        products = self.store.products("alice")
        self.assertEqual(1, len(products))
        self.assertEqual("Testmarke", products[0]["brand"])
        self.assertEqual("1 l", products[0]["pack_size"])
        self.assertEqual("1,49", products[0]["last_price"])
        self.assertEqual(["Lidl"], products[0]["known_stores"])
        self.assertEqual(0, products[0]["purchase_count"])

        favorite = self.store.set_product_favorite(products[0]["product_id"], "alice", True)
        self.assertTrue(favorite["favorite"])
        self.store.update_item(item["item_id"], "alice", {"status": "bought"})
        bought = self.store.products("alice")[0]
        self.assertEqual(1, bought["purchase_count"])
        self.assertTrue(bought["last_bought_at"])

    def test_product_memory_without_barcode_reuses_stable_text_identity(self):
        self.store.create_list("Woche", "alice", list_id="text-product")
        self.store.add_item("text-product", "Äpfel", "alice", {"brand": "Bio", "unit": "kg"})
        self.store.add_item("text-product", "Äpfel", "alice", {"brand": "Bio", "unit": "kg", "price": "2,99"})
        products = self.store.products("alice")
        self.assertEqual(1, len(products))
        self.assertEqual("2,99", products[0]["last_price"])

    def test_product_memory_is_private_to_actor(self):
        self.store.create_list("Familie", "alice", list_id="private-memory")
        self.store.share_list("private-memory", "alice", "bob", ["add"])
        self.store.add_item(
            "private-memory",
            "Batterien",
            "bob",
            {"barcode": "4006381333931"},
        )
        self.assertEqual([], self.store.products("alice"))
        self.assertEqual(["Batterien"], [row["name"] for row in self.store.products("bob")])

    def test_request_id_makes_reconnect_add_idempotent(self):
        self.store.create_list("Offline", "alice", list_id="offline")
        values = {"request_id": "offline-123", "quantity": "2"}
        first = self.store.add_item("offline", "Milch", "alice", values)
        second = self.store.add_item("offline", "Milch", "alice", values)
        self.assertEqual(first["item_id"], second["item_id"])
        self.assertEqual(1, len(self.store.items("alice", list_id="offline")))
        self.assertEqual(1, len(self.store.products("alice")))


if __name__ == "__main__":
    unittest.main()
