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


if __name__ == "__main__":
    unittest.main()
