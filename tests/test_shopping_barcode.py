import tempfile
import unittest
from pathlib import Path

from app.shopping_store import ShoppingStore, normalize_barcode


class ShoppingBarcodeTests(unittest.TestCase):
    def test_known_ean_upc_and_gtin_codes_are_accepted(self):
        self.assertEqual("4006381333931", normalize_barcode("4006 3813 3393 1"))
        self.assertEqual("96385074", normalize_barcode("96385074"))
        self.assertEqual("036000291452", normalize_barcode("036000291452"))
        self.assertEqual("10012345000017", normalize_barcode("10012345000017"))

    def test_invalid_check_digit_is_rejected(self):
        for code in ("4006381333932", "96385075", "036000291453", "10012345000018"):
            with self.assertRaises(ValueError):
                normalize_barcode(code)

    def test_store_reuses_local_barcode_knowledge_without_external_service(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ShoppingStore(Path(temp))
            store.create_list("Wocheneinkauf", "alice", list_id="weekly")
            created = store.add_item(
                "weekly", "Vollmilch", "alice",
                {"barcode": "4006381333931", "quantity": "1"},
            )
            known = store.find_known_barcode("alice", "4006381333931")
            self.assertIsNotNone(known)
            self.assertEqual(created["item_id"], known["item_id"])
            self.assertEqual("Vollmilch", known["name"])

    def test_barcode_lookup_respects_list_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ShoppingStore(Path(temp))
            store.create_list("Privat", "alice", list_id="private")
            store.add_item("private", "Secret item", "alice", {"barcode": "4006381333931"})
            self.assertIsNone(store.find_known_barcode("bob", "4006381333931"))


if __name__ == "__main__":
    unittest.main()
