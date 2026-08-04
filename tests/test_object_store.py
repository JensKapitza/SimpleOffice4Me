import tempfile
import unittest
from pathlib import Path

from app.object_store import ObjectStore


class ObjectStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ObjectStore(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_object_fields_notes_and_documents_are_persisted(self):
        item = self.store.create(
            {
                "name": "Notebook Jens",
                "type": "Gerät",
                "identifier": "SN-123",
                "tags": "it, mobil, it",
                "fields": "Hersteller=Lenovo\nModell=T14",
                "expires_at": "2030-12-31",
            },
            "jens",
        )
        self.store.attach_document(item["object_id"], "document-1", "jens")
        self.store.add_note(item["object_id"], "Akku getauscht", "jens")

        stored = self.store.object(item["object_id"])

        self.assertEqual(["it", "mobil"], stored["tags"])
        self.assertEqual("T14", stored["fields"]["Modell"])
        self.assertEqual(["document-1"], stored["document_ids"])
        self.assertEqual("Akku getauscht", stored["notes"][0]["text"])
        self.assertEqual("2030-12-31", stored["expires_at"])

    def test_update_search_and_detach_are_audited(self):
        item = self.store.create({"name": "KVM-01", "type": "Virtuelle Maschine"}, "jens")
        self.store.attach_document(item["object_id"], "document-1", "jens")
        updated = self.store.update(
            item["object_id"],
            {"name": "KVM-01", "type": "Virtuelle Maschine", "status": "inactive", "tags": "linux"},
            "peter",
        )
        self.store.detach_document(item["object_id"], "document-1", "peter")

        self.assertEqual("inactive", updated["status"])
        self.assertEqual(item["object_id"], self.store.objects("linux")[0]["object_id"])
        self.assertEqual([], self.store.object(item["object_id"])["document_ids"])
        history = list((Path(self.temp.name) / ".simpleoffice-history" / "events").glob("*.json"))
        self.assertGreaterEqual(len(history), 4)

    def test_invalid_custom_field_and_date_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "key=value"):
            self.store.create({"name": "Lizenz", "type": "Lizenz", "fields": "ohne Trenner"}, "jens")
        with self.assertRaisesRegex(ValueError, "ISO date"):
            self.store.create({"name": "Lizenz", "type": "Lizenz", "expires_at": "morgen"}, "jens")


if __name__ == "__main__":
    unittest.main()
