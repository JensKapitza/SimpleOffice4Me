import io
import os
import tarfile
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.archive_indexer import (
    _disk_budget,
    _windows_free_memory_bytes,
    cleanup_stale_scratch,
    index_archive,
    system_busy,
)
from app.archive_settings import (
    DEFAULT_ARCHIVE_DISK_RESERVE_MB,
    DEFAULT_ARCHIVE_MIN_FREE_MEMORY_MB,
)
from app.document_store import DocumentStore
from app.settings_store import SettingsStore


class ArchiveIndexerTest(unittest.TestCase):
    def test_archive_ocr_is_opt_in_and_persisted_setting_overrides_legacy_environment(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {
                "SIMPLEOFFICE_ARCHIVE_DEEP_INDEX": "1",
                "SIMPLEOFFICE_ARCHIVE_MEMBER_YIELD_MS": "0",
                "SIMPLEOFFICE_ARCHIVE_OCR": "0",
            },
            clear=False,
        ), patch("app.archive_indexer.system_busy", return_value=(False, "test resources")):
            root = Path(temp)
            store = DocumentStore(root)
            first_archive = root / "ocr-disabled.zip"
            with zipfile.ZipFile(first_archive, "w") as archive:
                archive.writestr("scan.png", b"synthetic image bytes")
            store.scan()
            with patch.object(store, "_image_ocr", return_value="archive OCR marker") as ocr:
                result = index_archive(store, first_archive)
            ocr.assert_not_called()
            self.assertNotIn("archive OCR marker", result["search_text"])

            SettingsStore(root).save(
                {"media": {"archive_ocr_enabled": True}}, "archive-admin",
            )
            second_archive = root / "ocr-enabled.zip"
            with zipfile.ZipFile(second_archive, "w") as archive:
                archive.writestr("scan.png", b"synthetic image bytes")
            store.scan()
            with patch.object(store, "_image_ocr", return_value="archive OCR marker") as ocr:
                result = index_archive(store, second_archive)
            ocr.assert_called_once()
            self.assertIn("archive OCR marker", result["search_text"])

    def test_zip_catalog_and_plain_text_become_searchable_without_bulk_extraction(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {"SIMPLEOFFICE_ARCHIVE_DEEP_INDEX": "1", "SIMPLEOFFICE_ARCHIVE_MEMBER_YIELD_MS": "0"},
            clear=False,
        ), patch("app.archive_indexer.system_busy", return_value=(False, "test resources")):
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
        ), patch("app.archive_indexer.system_busy", return_value=(False, "test resources")):
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

    def test_persisted_setting_disables_deep_index_even_with_legacy_environment_enabled(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"SIMPLEOFFICE_ARCHIVE_DEEP_INDEX": "1"}, clear=False,
        ):
            root = Path(temporary)
            archive_path = root / "vertrag.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("unterlagen/vertrag.txt", "Geheimer Suchbegriff Rotkehlchen")
            store = DocumentStore(root)
            store.scan()
            SettingsStore(root).save(
                {"media": {"archive_deep_index_enabled": True}}, "archive-admin",
            )
            with patch("app.archive_indexer.system_busy", return_value=(False, "test resources")):
                indexed = index_archive(store, archive_path)
            self.assertEqual("complete", indexed["status"])
            self.assertTrue(store.search("Rotkehlchen"))

            SettingsStore(root).save(
                {"media": {"archive_deep_index_enabled": False}}, "archive-admin",
            )
            result = index_archive(store, archive_path)

            self.assertEqual("catalog_only", result["status"])
            self.assertEqual(0, result["content_files_indexed"])
            self.assertTrue(store.search("vertrag.txt"))
            self.assertFalse(store.search("Rotkehlchen"))
            self.assertTrue(archive_path.is_file())
            tags = store.get_document(archive_path)["tags"]
            self.assertIn("archiv-catalog_only", tags)
            self.assertNotIn("archiv-inhalt-indexiert", tags)

            SettingsStore(root).save(
                {"media": {"archive_deep_index_enabled": True}}, "archive-admin",
            )
            with patch("app.archive_indexer.system_busy", return_value=(False, "test resources")):
                restored = index_archive(store, archive_path)
            self.assertEqual("complete", restored["status"])
            self.assertTrue(store.search("Rotkehlchen"))
            tags = store.get_document(archive_path)["tags"]
            self.assertIn("archiv-inhalt-indexiert", tags)
            self.assertNotIn("archiv-catalog_only", tags)

    def test_admin_load_threshold_is_applied_and_bounded(self):
        with patch("app.archive_indexer.os.cpu_count", return_value=1), patch(
            "app.archive_indexer.os.getloadavg", return_value=(2.0, 0.0, 0.0),
        ), patch("app.archive_indexer._free_memory_bytes", return_value=1024 * 1024 * 1024):
            busy, reason = system_busy(True, 0.75)
            self.assertTrue(busy)
            self.assertIn("system load", reason)
            self.assertEqual((False, "resources available"), system_busy(True, 8.0))
            for invalid in (True, 0.09, 8.01, float("nan"), float("inf")):
                with self.subTest(invalid=invalid):
                    self.assertEqual(
                        (True, "archive load threshold is invalid"),
                        system_busy(True, invalid),
                    )

    def test_legacy_enable_maps_to_resource_aware_indexing(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ,
            {
                "SIMPLEOFFICE_ARCHIVE_DEEP_INDEX": "1",
                "SIMPLEOFFICE_ARCHIVE_MAX_LOAD_PER_CPU": "0.75",
            },
        ):
            media = SettingsStore(temporary).settings()["media"]
            with patch("app.archive_indexer.os.cpu_count", return_value=1), patch(
                "app.archive_indexer.os.getloadavg", return_value=(2.0, 0.0, 0.0),
            ), patch("app.archive_indexer._free_memory_bytes", return_value=1024 * 1024 * 1024):
                busy, reason = system_busy(
                    media["archive_deep_index_enabled"],
                    media["archive_max_load_per_cpu"],
                )

        self.assertTrue(media["archive_deep_index_enabled"])
        self.assertEqual(0.75, media["archive_max_load_per_cpu"])
        self.assertTrue(busy)
        self.assertIn("system load", reason)

    def test_memory_and_disk_limits_migrate_persist_and_bound_legacy_values(self):
        cases = (
            (
                "SIMPLEOFFICE_ARCHIVE_MIN_FREE_MEMORY_MB",
                "archive_min_free_memory_mb", 1024, 64, 65536,
                DEFAULT_ARCHIVE_MIN_FREE_MEMORY_MB,
            ),
            (
                "SIMPLEOFFICE_ARCHIVE_DISK_RESERVE_MB",
                "archive_disk_reserve_mb", 8192, 128, 1048576,
                DEFAULT_ARCHIVE_DISK_RESERVE_MB,
            ),
        )
        for env_name, key, configured, minimum, maximum, default in cases:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temporary:
                store = SettingsStore(temporary)
                with patch.dict(os.environ, {env_name: str(configured)}):
                    self.assertEqual(configured, store.settings()["media"][key])
                    store.save({"media": {"preview_enabled": False}}, "archive-admin")
                with patch.dict(os.environ, {env_name: str(default)}):
                    self.assertEqual(configured, store.settings()["media"][key])

            for raw, expected in (
                (str(minimum - 1), minimum),
                (str(maximum + 1), maximum),
                ("invalid", default),
                ("9" * 30, default),
            ):
                with self.subTest(key=key, raw=raw), tempfile.TemporaryDirectory() as temporary:
                    with patch.dict(os.environ, {env_name: raw}):
                        self.assertEqual(
                            expected, SettingsStore(temporary).settings()["media"][key],
                        )

    def test_resource_settings_are_used_and_changes_reindex_existing_archives(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "resource-policy.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("document.txt", "resource policy marker")
            store = DocumentStore(root)
            store.scan()
            settings = SettingsStore(root)
            settings.save(
                {"media": {
                    "archive_deep_index_enabled": True,
                    "archive_max_load_per_cpu": 0.75,
                    "archive_min_free_memory_mb": 512,
                    "archive_disk_reserve_mb": 8192,
                }},
                "archive-admin",
            )
            with patch("app.archive_indexer.system_busy", return_value=(False, "test")) as busy, patch(
                "app.archive_indexer._disk_budget", return_value=(10_000_000_000, 1_000_000_000),
            ) as budget:
                first = index_archive(store, archive_path)
            busy.assert_called_once_with(True, 0.75, 512)
            self.assertEqual(8192, budget.call_args.args[1])
            self.assertEqual(512, first["resource_policy"]["min_free_memory_mb"])
            self.assertEqual(8192, first["resource_policy"]["disk_reserve_mb"])

            settings.save(
                {"media": {
                    "archive_deep_index_enabled": True,
                    "archive_max_load_per_cpu": 0.75,
                    "archive_min_free_memory_mb": 1024,
                    "archive_disk_reserve_mb": 4096,
                }},
                "archive-admin",
            )
            with patch("app.archive_indexer.system_busy", return_value=(False, "test")) as busy, patch(
                "app.archive_indexer._disk_budget", return_value=(10_000_000_000, 1_000_000_000),
            ) as budget:
                second = index_archive(store, archive_path)
            busy.assert_called_once_with(True, 0.75, 1024)
            self.assertEqual(4096, budget.call_args.args[1])
            self.assertEqual(1024, second["resource_policy"]["min_free_memory_mb"])
            self.assertEqual(4096, second["resource_policy"]["disk_reserve_mb"])

    def test_memory_threshold_and_disk_reserve_fail_closed_at_configured_limits(self):
        with patch("app.archive_indexer.os.cpu_count", return_value=1), patch(
            "app.archive_indexer.os.getloadavg", return_value=(0.0, 0.0, 0.0),
        ), patch("app.archive_indexer._free_memory_bytes", return_value=128 * 1024 * 1024):
            self.assertEqual((False, "resources available"), system_busy(True, 0.75, 64))
            busy, reason = system_busy(True, 0.75, 256)
            self.assertTrue(busy)
            self.assertIn("memory", reason)
            self.assertEqual(
                (True, "archive memory threshold is invalid"),
                system_busy(True, 0.75, True),
            )
        with patch("app.archive_indexer._free_memory_bytes", return_value=None):
            self.assertEqual(
                (True, "available memory could not be determined"),
                system_busy(True, 0.75, 512),
            )

        usage = SimpleNamespace(free=10 * 1024 * 1024 * 1024)
        with tempfile.TemporaryDirectory() as temporary, patch(
            "app.archive_indexer.shutil.disk_usage", return_value=usage,
        ):
            free, budget = _disk_budget(Path(temporary), 9728)
            self.assertEqual(usage.free, free)
            self.assertEqual(512 * 1024 * 1024, budget)
            self.assertEqual((usage.free, 0), _disk_budget(Path(temporary), True))

    def test_windows_available_memory_probe_reads_physical_memory_and_handles_failure(self):
        class MemoryStatusApi:
            def __init__(self, available, result=True):
                self.available = available
                self.result = result

            def GlobalMemoryStatusEx(self, status_pointer):
                status_pointer._obj.ullAvailPhys = self.available
                return self.result

        self.assertEqual(
            512 * 1024 * 1024,
            _windows_free_memory_bytes(MemoryStatusApi(512 * 1024 * 1024)),
        )
        self.assertIsNone(_windows_free_memory_bytes(MemoryStatusApi(0, result=False)))

    def test_suspicious_compression_ratio_is_catalogued_but_not_inflated_for_text(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {
                "SIMPLEOFFICE_ARCHIVE_DEEP_INDEX": "1",
                "SIMPLEOFFICE_ARCHIVE_MAX_COMPRESSION_RATIO": "10",
                "SIMPLEOFFICE_ARCHIVE_MEMBER_YIELD_MS": "0",
            },
            clear=False,
        ), patch("app.archive_indexer.system_busy", return_value=(False, "test resources")):
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
