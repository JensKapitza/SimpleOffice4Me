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
