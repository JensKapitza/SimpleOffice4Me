import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.v2.metadata import MetadataSource
from app.v2.metadata_migration import (
    V2MetadataMigrationStore,
    build_legacy_metadata_projection,
    legacy_metadata_envelope,
)
from app.v2.migration import (
    create_migration_backup,
    transfer_legacy_documents,
    verify_migration_transfer,
)


class V2MetadataMigrationTests(unittest.TestCase):
    def test_legacy_metadata_maps_original_aliases_and_federation_provenance(self):
        content = b"payload"
        digest = hashlib.sha256(content).hexdigest()
        metadata = {
            "document_id": "doc-meta-1",
            "first_seen_at": "2026-01-01T10:00:00Z",
            "last_seen_at": "2026-02-01T10:00:00Z",
            "original_sha256": digest,
            "sha256": digest,
            "last_path": "Archive/Renamed.PDF",
            "location_history": [
                {
                    "from": "Inbox/Report.PDF",
                    "to": "Archive/Renamed.PDF",
                    "at": "2026-01-15T10:00:00Z",
                    "actor": "tester",
                }
            ],
            "tags": ["invoice"],
            "attributes": {
                "federation_origin": {
                    "peer_id": "peer-a",
                    "remote_document_id": "remote-1",
                }
            },
        }

        envelope = legacy_metadata_envelope(
            metadata,
            current_path="Archive/Renamed.PDF",
            current_size=len(content),
            current_sha256=digest,
        )

        self.assertEqual("doc-meta-1", envelope.object_id.value)
        self.assertEqual("Report.PDF", envelope.values_for("filename")[0].value)
        self.assertEqual(
            {"Report.PDF", "Renamed.PDF"},
            {alias.name for alias in envelope.aliases},
        )
        self.assertEqual(1, len(envelope.federated))
        self.assertEqual(MetadataSource.FEDERATED, envelope.federated[0].provenance.source)
        self.assertEqual("peer:peer-a", envelope.federated[0].provenance.source_ref)
        self.assertEqual(digest, envelope.sidecar["legacy_metadata_sha256"])

    def test_projection_store_is_idempotent_and_uses_hashed_filename(self):
        digest = hashlib.sha256(b"payload").hexdigest()
        projection = build_legacy_metadata_projection(
            {
                "document_id": "folder-like/object",
                "last_path": "safe/file.bin",
                "sha256": digest,
            },
            current_path="safe/file.bin",
            current_size=7,
            current_sha256=digest,
        )
        with tempfile.TemporaryDirectory() as temp:
            store = V2MetadataMigrationStore(temp)
            first = store.write(projection)
            second = store.write(projection)
            target = store.path_for("folder-like/object")

            self.assertEqual("created", first)
            self.assertEqual("unchanged", second)
            self.assertEqual(64, len(target.stem))
            self.assertEqual(target.parent, Path(temp) / ".simpleoffice-v2" / "metadata")
            self.assertTrue(store.verify(projection))

    def test_transfer_projects_metadata_without_modifying_v1_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "documents"
            root.mkdir()
            content = b"legacy-metadata"
            digest = hashlib.sha256(content).hexdigest()
            document = root / "archive" / "renamed.txt"
            document.parent.mkdir()
            document.write_bytes(content)
            metadata_dir = root / ".simpleoffice-meta" / "documents"
            metadata_dir.mkdir(parents=True)
            metadata_path = metadata_dir / "doc-meta-transfer.json"
            metadata = {
                "version": 1,
                "document_id": "doc-meta-transfer",
                "first_seen_at": "2026-01-01T00:00:00Z",
                "last_seen_at": "2026-02-01T00:00:00Z",
                "last_path": "archive/renamed.txt",
                "sha256": digest,
                "original_sha256": digest,
                "location_history": [
                    {
                        "from": "inbox/original.txt",
                        "to": "archive/renamed.txt",
                        "at": "2026-01-15T00:00:00Z",
                    }
                ],
                "tags": ["legacy"],
            }
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            before = metadata_path.read_bytes()
            backup = base / "backup"
            create_migration_backup(root, backup)

            first = transfer_legacy_documents(root, backup)
            second = transfer_legacy_documents(root, backup)
            verification = verify_migration_transfer(root)

            self.assertEqual(1, first["metadata_created_documents"])
            self.assertEqual(0, first["metadata_unchanged_documents"])
            self.assertEqual(0, second["metadata_created_documents"])
            self.assertEqual(1, second["metadata_unchanged_documents"])
            self.assertEqual(before, metadata_path.read_bytes())
            self.assertTrue(verification["ready"])
            self.assertEqual(1, verification["verified_documents"])
            self.assertEqual(1, verification["verified_metadata_documents"])

            projection = V2MetadataMigrationStore(root).read("doc-meta-transfer")
            original = projection["envelope"]["original"]
            self.assertEqual(
                "original.txt",
                next(item["value"] for item in original if item["name"] == "filename"),
            )

    def test_verification_fails_if_v2_metadata_projection_is_tampered(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "documents"
            root.mkdir()
            content = b"metadata-integrity"
            digest = hashlib.sha256(content).hexdigest()
            document = root / "inbox" / "file.bin"
            document.parent.mkdir()
            document.write_bytes(content)
            metadata_dir = root / ".simpleoffice-meta" / "documents"
            metadata_dir.mkdir(parents=True)
            (metadata_dir / "doc-meta-tamper.json").write_text(
                json.dumps({
                    "document_id": "doc-meta-tamper",
                    "last_path": "inbox/file.bin",
                    "sha256": digest,
                }),
                encoding="utf-8",
            )
            backup = base / "backup"
            create_migration_backup(root, backup)
            transfer_legacy_documents(root, backup)

            store = V2MetadataMigrationStore(root)
            target = store.path_for("doc-meta-tamper")
            projection = json.loads(target.read_text(encoding="utf-8"))
            projection["envelope"]["observed"][0]["value"] = "changed/path.bin"
            target.write_text(json.dumps(projection), encoding="utf-8")

            verification = verify_migration_transfer(root)

            self.assertFalse(verification["ready"])
            self.assertTrue(any(
                "metadata projection differs" in blocker
                for blocker in verification["blockers"]
            ))


if __name__ == "__main__":
    unittest.main()
