from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.document_store import DocumentStore
from app.v2.catalog import ObjectCatalog
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.materialize import materialize_verified_object
from app.v2.migration import create_migration_backup, transfer_legacy_documents


class V2VerifiedMaterializationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        (self.root / "inbox").mkdir(parents=True)
        self.source = self.root / "inbox" / "movie.mp4"
        self.source.write_bytes(b"verified-video-content")
        self.documents = DocumentStore(self.root)
        self.documents.scan()
        self.document = self.documents.get_document(self.source)
        backup = self.base / "backup"
        create_migration_backup(self.root, backup)
        transfer_legacy_documents(self.root, backup)
        prepare_shadow(self.root, apply=True, acknowledge_local_plaintext=True)
        activate_v2(self.root, apply=True, acknowledge_local_plaintext=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_materialization_reads_v2_after_plaintext_projection_is_missing(self):
        self.source.unlink()
        entry = ObjectCatalog(self.root).get(self.document["document_id"])
        self.assertTrue(entry.ok)

        materialized_parent = None
        with materialize_verified_object(
            self.root,
            "preview-worker",
            self.document["document_id"],
            suffix=".mp4",
        ) as path:
            materialized_parent = path.parent
            self.assertTrue(path.is_file())
            self.assertEqual(".mp4", path.suffix)
            self.assertEqual(b"verified-video-content", path.read_bytes())
            self.assertFalse(self.root in path.parents)

        self.assertIsNotNone(materialized_parent)
        self.assertFalse(materialized_parent.exists())

    def test_materialization_rejects_path_like_suffixes(self):
        with self.assertRaisesRegex(ValueError, "suffix"):
            with materialize_verified_object(
                self.root,
                "preview-worker",
                self.document["document_id"],
                suffix="../mp4",
            ):
                pass


if __name__ == "__main__":
    unittest.main()
