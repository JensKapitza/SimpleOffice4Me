import tempfile
import unittest
from pathlib import Path

from app import app
from app.contact_audit import LABELS, change_history
from app.contact_store import ContactStore
from app.document_store import atomic_json_write


class ContactAuditTests(unittest.TestCase):
    def test_audit_route_requires_login_and_has_bilingual_labels(self):
        response = app.test_client().get("/documents/contacts/history")

        self.assertEqual(302, response.status_code)
        self.assertIn("/auth/login", response.headers["Location"])
        self.assertEqual("Änderungshistorie", LABELS["de"]["title"])
        self.assertEqual("Change history", LABELS["en"]["title"])

    def test_history_only_contains_contacts_visible_to_user(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            shared = store.upsert({"display_name": "Gemeinsam", "email": "alt@example.test"}, "admin")
            store.share(shared["contact_id"], ["jens"], "admin")
            store.upsert({"display_name": "Gemeinsam", "email": "neu@example.test"}, "jens", shared["contact_id"])
            store.upsert({"display_name": "Versteckt", "email": "secret@example.test"}, "other")

            history = change_history(store, "jens")

            self.assertTrue(history["entries"])
            self.assertEqual({"Gemeinsam"}, {entry["display_name"] for entry in history["entries"]})
            self.assertNotIn("secret@example.test", {entry["new"] for entry in history["entries"]})

    def test_history_filters_and_paginates_without_losing_total(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            contact = store.upsert({"display_name": "Amy Beispiel", "email": "eins@example.test"}, "admin")
            store.upsert({"display_name": "Amy Beispiel", "email": "zwei@example.test"}, "admin", contact["contact_id"])
            store.upsert({"display_name": "Amy Beispiel", "email": "drei@example.test"}, "admin", contact["contact_id"])

            first = change_history(store, "admin", query="example.test", editor="admin", field="email", limit=1)
            second = change_history(store, "admin", query="example.test", editor="admin", field="email", offset=1, limit=1)

            self.assertEqual(3, first["total"])
            self.assertEqual(1, len(first["entries"]))
            self.assertEqual(1, len(second["entries"]))
            self.assertNotEqual(first["entries"][0]["new"], second["entries"][0]["new"])
            self.assertEqual(["admin"], first["editors"])
            self.assertIn("email", first["fields"])

    def test_history_limit_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            contact = store.upsert({"display_name": "Grenze", "email": "0@example.test"}, "admin")
            payload = store._read(store.contacts_path, {"contacts": []})
            payload["contacts"][0]["changes"] = [{"field": "email", "old": f"{index}@example.test", "new": f"{index + 1}@example.test", "at": f"2026-08-04T12:{index // 60:02d}:{index % 60:02d}+00:00", "actor": "admin"} for index in range(120)]
            atomic_json_write(store.contacts_path, payload)

            history = change_history(store, "admin", limit=10_000)

            self.assertGreater(history["total"], 100)
            self.assertEqual(100, len(history["entries"]))


if __name__ == "__main__":
    unittest.main()
