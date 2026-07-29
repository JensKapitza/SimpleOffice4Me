import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.document_store import DocumentStore
from app.replication_store import ReplicationStore


class ReplicationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "source"; self.root.mkdir()
        self.target = Path(self.temp.name) / "target"; self.target.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_tagged_document_is_mirrored_with_manifest_and_updated(self):
        source = self.root / "rechnung.txt"; source.write_text("erste Version", encoding="utf-8")
        documents = DocumentStore(self.root); documents.scan(); document = documents.get_document(source)
        documents.set_tags(document["document_id"], ["rechnung"], "tester")
        replication = ReplicationStore(self.root)
        target = replication.add_target({"label": "USB", "path": str(self.target)}, "tester")
        rule = replication.add_rule({"label": "Rechnungen", "target_id": target["target_id"], "categories": ["documents"], "tags": "rechnung"}, "tester")

        first = replication.run_rule(rule["rule_id"], "tester")
        copy = self.target / "SimpleOffice-Spiegelung" / "documents" / document["document_id"] / "rechnung.txt"
        self.assertEqual("erste Version", copy.read_text(encoding="utf-8"))
        self.assertEqual(1, first["copied"])
        self.assertEqual(1, replication.run_rule(rule["rule_id"], "tester")["unchanged"])

        manifest = json.loads(Path(first["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(document["document_id"], manifest["files"][0]["document_id"])

    def test_unavailable_target_is_rejected(self):
        replication = ReplicationStore(self.root)
        with self.assertRaisesRegex(ValueError, "Zielname"):
            replication.add_target({"label": "fehlend", "path": str(self.target / "missing")}, "tester")

    def test_existing_target_is_imported_without_changing_source(self):
        legacy = self.target / "Altbestand" / "rechnung.txt"; legacy.parent.mkdir()
        legacy.write_text("bleibt erhalten", encoding="utf-8")

        target = ReplicationStore(self.root).add_target({"label": "Altplatte", "path": str(self.target)}, "tester")

        self.assertEqual("bleibt erhalten", legacy.read_text(encoding="utf-8"))
        self.assertEqual(1, target["initial_import"]["copied"])
        imported = self.root / "imports" / "Altplatte" / "Altbestand" / "rechnung.txt"
        self.assertEqual("bleibt erhalten", imported.read_text(encoding="utf-8"))

    def test_existing_target_can_be_imported_later(self):
        replication = ReplicationStore(self.root)
        target = replication.add_target({"label": "USB", "path": str(self.target)}, "tester")
        late = self.target / "später.txt"; late.write_text("neu", encoding="utf-8")

        result = replication.import_target(target["target_id"], "tester")

        self.assertEqual(1, result["copied"])
        self.assertEqual("neu", (self.root / "imports" / "USB" / "später.txt").read_text(encoding="utf-8"))

    def test_paused_target_blocks_import_and_replication(self):
        replication = ReplicationStore(self.root)
        target = replication.add_target({"label": "USB", "path": str(self.target)}, "tester")
        rule = replication.add_rule({"label": "Kontakte", "target_id": target["target_id"], "categories": ["contacts"]}, "tester")
        replication.set_target_enabled(target["target_id"], False, "tester")

        with self.assertRaisesRegex(ValueError, "pausiert"):
            replication.import_target(target["target_id"], "tester")
        with self.assertRaisesRegex(ValueError, "pausiert"):
            replication.run_rule(rule["rule_id"], "tester")
        self.assertTrue(replication.set_target_enabled(target["target_id"], True, "tester")["enabled"])

    def test_unreadable_file_is_skipped_without_aborting_import(self):
        readable = self.target / "ok.txt"; unreadable = self.target / "locked.bin"
        readable.write_text("ok", encoding="utf-8"); unreadable.write_bytes(b"locked")
        original_hash = __import__("app.document_store", fromlist=["sha256_file"]).sha256_file
        with patch("app.document_store.sha256_file", side_effect=lambda path: (_ for _ in ()).throw(PermissionError("denied")) if Path(path) == unreadable else original_hash(path)):
            target = ReplicationStore(self.root).add_target({"label": "USB", "path": str(self.target)}, "tester")

        self.assertEqual(1, target["initial_import"]["copied"])
        self.assertEqual(1, target["initial_import"]["errors"])

    def test_run_all_collects_enabled_rules(self):
        replication = ReplicationStore(self.root)
        target = replication.add_target({"label": "USB", "path": str(self.target)}, "tester")
        rule = replication.add_rule({"label": "Kontakte", "target_id": target["target_id"], "categories": ["contacts"]}, "tester")
        result = replication.run_all("system")
        self.assertEqual(rule["rule_id"], result["completed"][0]["rule_id"])
        self.assertEqual([], result["errors"])
