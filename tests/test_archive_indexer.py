import io
import os
import tarfile
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.archive_indexer import cleanup_stale_scratch, index_archive
from app.document_store import DocumentStore


class ArchiveIndexerTest(unittest.TestCase):
    def test_zip_catalog_and_plain_text_become_searchable_without_bulk_extraction(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {"SIMPLEOFFICE_ARCHIVE_DEEP_INDEX": "1", "SIMPLEOFFICE_ARCHIVE_MEMBER_YIELD_MS": "0"},
            clear=False,
        ):
            root = Path(temp)
            archive_path = root / "kundenunterlagen.zip"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("Projekt-A/vertrag.txt", "Besondere Kuendigungsfrist Kranich 42")
                archive.writestr("Projekt-A/rechnung-2026.pdf", b"not a real pdf")

            store = DocumentStore(root)
            store.scan()
            with patch("zipfile.ZipFile.extractall", side_effect=AssertionError("bulk extraction forbidden")):
                result = index_archive(store, archive_path)

            self.assertIsNotNone(result)
            self.assertEqual("zip", result["archive_format"])
            self.assertEqual(2, result["member_count"])
            self.assertEqual("complete", result["status"])
            self.assertEqual(1, result["extraction_errors"])
            self.assertGreaterEqual(result["content_files_indexed"], 1)
            self.assertTrue(store.search("Kranich"))
            self.assertTrue(store.search("rechnung-2026.pdf"))
            document = store.get_document(archive_path)
            self.assertIn("archiv", document["tags"])
            self.assertIn("archiv-zip", document["tags"])
            self.assertEqual(document["sha256"], document["attributes"]["archive_index"]["source_sha256"])

    def test_tar_member_text_is_indexed_one_member_at_a_time(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {"SIMPLEOFFICE_ARCHIVE_DEEP_INDEX": "1", "SIMPLEOFFICE_ARCHIVE_MEMBER_YIELD_MS": "0"},
            clear=False,
        ):
            root = Path(temp)
            archive_path = root / "export.tar.gz"
            payload = b"Interner Suchbegriff Albatros-987"
            with tarfile.open(archive_path, "w:gz") as archive:
                info = tarfile.TarInfo("berichte/bericht.txt")
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))

            store = DocumentStore(root)
            store.scan()
            result = index_archive(store, archive_path)

            self.assertEqual("tar", result["archive_format"])
            self.assertEqual(1, result["content_files_indexed"])
            self.assertTrue(store.search("Albatros-987"))
            self.assertTrue(store.search("berichte/bericht.txt"))

    def test_busy_system_keeps_catalog_searchable_and_defers_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "spaeter.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("wichtig/uebergabe.txt", "Nur im Tiefenindex: Mondsichel-123")

            store = DocumentStore(root)
            store.scan()
            with patch("app.archive_indexer.system_busy", return_value=(True, "interactive load")):
                result = index_archive(store, archive_path)

            self.assertEqual("catalog_only", result["status"])
            self.assertEqual(0, result["content_files_indexed"])
            self.assertTrue(store.search("uebergabe.txt"))
            self.assertFalse(store.search("Mondsichel-123"))

            with patch("app.archive_indexer.system_busy", return_value=(False, "resources available")):
                result = index_archive(store, archive_path)
            self.assertEqual("complete", result["status"])
            self.assertTrue(store.search("Mondsichel-123"))

    def test_suspicious_compression_ratio_is_catalogued_but_not_inflated_for_text(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {
                "SIMPLEOFFICE_ARCHIVE_DEEP_INDEX": "1",
                "SIMPLEOFFICE_ARCHIVE_MAX_COMPRESSION_RATIO": "10",
                "SIMPLEOFFICE_ARCHIVE_MEMBER_YIELD_MS": "0",
            },
            clear=False,
        ):
            root = Path(temp)
            archive_path = root / "bomb-like.zip"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("huge.txt", "A" * (2 * 1024 * 1024))

            store = DocumentStore(root)
            store.scan()
            result = index_archive(store, archive_path)

            self.assertGreaterEqual(result["skipped_suspicious_compression"], 1)
            self.assertEqual(0, result["content_files_indexed"])
            self.assertTrue(store.search("huge.txt"))

    def test_stale_private_scratch_is_removed_after_interrupted_run(self):
        with tempfile.TemporaryDirectory() as temp:
            store = DocumentStore(temp)
            store.initialize()
            scratch = store.control / "archive-index-scratch"
            abandoned = scratch / "job-abandoned"
            abandoned.mkdir(parents=True)
            secret = abandoned / "member.bin"
            secret.write_bytes(b"sensitive")
            old = time.time() - 2 * 24 * 3600
            os.utime(secret, (old, old))
            os.utime(abandoned, (old, old))

            removed = cleanup_stale_scratch(store, max_age_seconds=3600)

            self.assertGreaterEqual(removed, 1)
            self.assertFalse(abandoned.exists())


if __name__ == "__main__":
    unittest.main()
