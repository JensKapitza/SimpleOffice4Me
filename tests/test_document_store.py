import json
import tempfile
import unittest
from pathlib import Path

from app.document_store import CONTROL_DIR, POLICY_FILE, DocumentStore


class DocumentStoreTest(unittest.TestCase):
    def test_scan_creates_metadata_and_detects_duplicate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "one.txt").write_text("same content", encoding="utf-8")
            (root / "two.txt").write_text("same content", encoding="utf-8")

            report = DocumentStore(root).scan()

            self.assertEqual(2, report.files)
            self.assertEqual(2, report.new_files)
            self.assertEqual(1, report.duplicates)
            self.assertTrue((root / POLICY_FILE).exists())
            self.assertTrue((root / CONTROL_DIR / "events.ndjson").exists())

    def test_existing_policy_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            policy = {"version": 1, "folder_id": "known", "inherit": False, "grants": []}
            (root / POLICY_FILE).write_text(json.dumps(policy), encoding="utf-8")

            DocumentStore(root).initialize()

            self.assertEqual(policy, json.loads((root / POLICY_FILE).read_text(encoding="utf-8")))

    def test_symlink_is_logged_but_not_followed_by_default(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "source.txt").write_text("content", encoding="utf-8")
            (root / "loop").symlink_to(root, target_is_directory=True)

            report = DocumentStore(root).scan()

            self.assertEqual(1, report.files)
            self.assertEqual(1, report.symlinks)

    def test_notes_links_states_and_versions_are_file_based(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.txt"
            source.write_text("version one", encoding="utf-8")
            related = root / "related.txt"
            related.write_text("related", encoding="utf-8")
            replacement = root / "replacement.txt"
            replacement.write_text("version two", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()

            first = store.get_document("source.txt")
            second = store.get_document("related.txt")
            note = store.add_note(first["document_id"], "Bitte prüfen", "tester")
            store.set_state(first["document_id"], "in_pruefung", "tester")
            store.set_attribute(first["document_id"], "projekt", "Musterbau", "tester")
            store.add_link(first["document_id"], second["document_id"], "bezieht_sich_auf", author="tester")
            version = store.import_version(replacement, first["document_id"], "tester")
            graph = store.graph(first["document_id"])

            self.assertEqual("Bitte prüfen", note["text"])
            self.assertEqual("in_pruefung", store.get_document(first["document_id"])["state"])
            self.assertEqual(2, version["version_number"])
            self.assertIn(version["document_id"], {node["id"] for node in graph["nodes"]})
            self.assertEqual(first["document_id"], store.search("Musterbau")[0]["document_id"])

    def test_wiki_versions_and_logbook_are_readable_from_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_file = root / "angebot.txt"
            first_file.write_text("erste Fassung", encoding="utf-8")
            replacement = root / "angebot-neu.txt"
            replacement.write_text("zweite Fassung", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            first = store.get_document("angebot.txt")
            store.add_note(first["document_id"], "Kunde hat bestätigt", "tester")
            store.import_version(replacement, first["document_id"], "tester")

            self.assertEqual(2, len(store.versions(first["document_id"])))
            self.assertEqual("Kunde hat bestätigt", store.note_wiki()[0]["text"])
            self.assertTrue(any(event.get("action") == "document_note_added" for event in store.logbook(first["document_id"])))

    def test_changed_original_is_reported_as_integrity_problem(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.txt"
            source.write_text("original", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            source.write_text("changed outside application", encoding="utf-8")

            store.scan()

            self.assertEqual("integrity_changed", store.get_document("source.txt")["system_state"])
