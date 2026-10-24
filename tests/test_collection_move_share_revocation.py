"""Regression coverage for public shares after a physical collection MOVE."""
import tempfile
import unittest
from pathlib import Path

from app.document_store import DocumentStore


class CollectionMoveShareRevocationTest(unittest.TestCase):
    def test_nested_collection_move_invalidates_existing_share_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "source" / "nested").mkdir(parents=True)
            (root / "source" / "first.txt").write_text("first", encoding="utf-8")
            (root / "source" / "nested" / "second.txt").write_text("second", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            documents = [
                store.get_document("source/first.txt"),
                store.get_document("source/nested/second.txt"),
            ]
            password = "a-long-test-password"
            shares = [
                store.create_share(doc["document_id"], password, 1, "admin")
                for doc in documents
            ]
            for share in shares:
                self.assertTrue(store.open_share(share["share_id"], password))
            store.move_collection("source", "destination", "admin")
            for doc, share in zip(documents, shares):
                moved = store.get_document(doc["document_id"])
                self.assertEqual(doc.get("location_revision", 0) + 1, moved["location_revision"])
                self.assertTrue(moved["location_last_moved_at"])
                self.assertTrue(moved["last_path"].startswith("destination/"))
                with self.assertRaises(ValueError):
                    store.open_share(share["share_id"], password)


if __name__ == "__main__":
    unittest.main()
