import tempfile
import unittest
from pathlib import Path

from app.document_store import DocumentStore
from app.v2.adapters.authoritative import V2AuthoritativeStorageAdapter
from app.v2.blob_store import BlobStore
from app.v2.catalog import ObjectCatalog
from app.v2.contracts import LogicalObjectId
from app.v2.cutover import activate_v2, load_cutover_state, mark_shadow_dirty, prepare_shadow
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.storage_runtime import create_document, storage_for


class V2AuthoritativeCutoverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        (self.root / "inbox").mkdir(parents=True)
        (self.root / "inbox" / "seed.txt").write_bytes(b"seed")
        self.legacy = DocumentStore(self.root)
        self.legacy.scan()
        backup = self.base / "backup"
        create_migration_backup(self.root, backup)
        transfer_legacy_documents(self.root, backup)
        prepare_shadow(
            self.root,
            apply=True,
            acknowledge_local_plaintext=True,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_activation_requires_clean_verified_shadow_and_explicit_ack(self):
        with self.assertRaisesRegex(ValueError, "explicit acknowledgement"):
            activate_v2(self.root, apply=True)
        self.assertEqual("shadow", load_cutover_state(self.root).mode)

        result = activate_v2(
            self.root,
            apply=True,
            acknowledge_local_plaintext=True,
        )
        self.assertEqual("v2", result["mode"])
        self.assertTrue(result["verification_ready"])
        self.assertEqual("v2", load_cutover_state(self.root).mode)

    def test_dirty_shadow_cannot_be_promoted(self):
        mark_shadow_dirty(self.root, "synthetic test divergence")
        with self.assertRaisesRegex(ValueError, "dirty"):
            activate_v2(
                self.root,
                apply=True,
                acknowledge_local_plaintext=True,
            )
        self.assertEqual("shadow", load_cutover_state(self.root).mode)

    def test_runtime_uses_authoritative_v2_and_keeps_compat_projection(self):
        activate_v2(
            self.root,
            apply=True,
            acknowledge_local_plaintext=True,
        )
        self.assertIsInstance(
            storage_for(self.root, "tester"),
            V2AuthoritativeStorageAdapter,
        )

        created = create_document(
            self.root,
            "tester",
            "inbox/v2-authoritative.txt",
            b"authoritative",
            max_bytes=100,
        )
        object_id = LogicalObjectId(created["document_id"])
        catalog = ObjectCatalog(self.root).get(object_id)
        self.assertTrue(catalog.ok)
        self.assertEqual("inbox/v2-authoritative.txt", catalog.value.location.relative_path)
        self.assertEqual(
            b"authoritative",
            BlobStore(self.root).read(object_id, version_id=catalog.value.version_id),
        )
        self.assertEqual(
            b"authoritative",
            (self.root / "inbox" / "v2-authoritative.txt").read_bytes(),
        )
        projected = DocumentStore(self.root).get_document(created["document_id"])
        self.assertEqual(created["document_id"], projected["document_id"])


if __name__ == "__main__":
    unittest.main()
