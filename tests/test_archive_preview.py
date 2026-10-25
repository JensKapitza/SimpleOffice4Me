"""Regression tests for bounded, extraction-free ZIP and EPUB previews."""
from __future__ import annotations

import hashlib
import io
import stat
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.archive_preview import archive_preview
from app.preview_service import PreviewService, SUPPORTED_SUFFIXES
from app.settings_store import SettingsStore


def _zip_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries:
            archive.writestr(name, payload)
    return output.getvalue()


def _epub_bytes(*, unsafe: bool = False) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/package.opf"/></rootfiles></container>',
        )
        archive.writestr(
            "OEBPS/package.opf",
            '<package xmlns="http://www.idpf.org/2007/opf"><metadata>'
            "<title>Archive sample</title><creator>Preview author</creator></metadata>"
            '<manifest><item id="chapter" href="chapter.xhtml" '
            'media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="chapter"/></spine></package>',
        )
        archive.writestr(
            "OEBPS/chapter.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Text only</p></body></html>',
        )
        if unsafe:
            archive.writestr("../escape.txt", b"outside")
    return output.getvalue()


class ArchivePreviewTests(unittest.TestCase):
    def test_zip_preview_omits_traversal_and_symlink_without_extracting(self):
        output = io.BytesIO()
        link = zipfile.ZipInfo("linked.txt")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        special = zipfile.ZipInfo("safe/fifo")
        special.create_system = 3
        special.external_attr = (stat.S_IFIFO | 0o600) << 16
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("safe/facts.txt", b"metadata only")
            archive.writestr("../escape.txt", b"outside")
            archive.writestr("C:\\drive.txt", b"outside")
            archive.writestr(link, "safe/facts.txt")
            archive.writestr(special, b"not a regular file")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sample.zip"
            path.write_bytes(output.getvalue())

            preview = archive_preview(path, ".zip")

        self.assertEqual("ready", preview["status"])
        self.assertEqual(5, preview["entry_count"])
        self.assertEqual(1, preview["file_count"])
        self.assertEqual(3, preview["unsafe_entries"])
        self.assertEqual(1, preview["symlink_entries"])
        self.assertFalse(preview["truncated"])
        self.assertEqual(["safe/facts.txt"], [row["name"] for row in preview["items"]])

    def test_duplicate_zip_members_are_omitted_and_counted(self):
        output = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("same.txt", b"first")
                archive.writestr("same.txt", b"duplicate")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "duplicate.zip"
            path.write_bytes(output.getvalue())

            preview = archive_preview(path, ".zip")

        self.assertEqual(2, preview["entry_count"])
        self.assertEqual(1, preview["file_count"])
        self.assertEqual(1, preview["duplicate_entries"])
        self.assertEqual(["same.txt"], [item["name"] for item in preview["items"]])

    def test_zip_listing_truncates_only_valid_displayable_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sample.zip"
            path.write_bytes(_zip_bytes([
                ("one.txt", b"1"), ("../unsafe.txt", b"x"), ("two.txt", b"22"),
            ]))
            with patch("app.archive_preview.MAX_ARCHIVE_DISPLAY_ITEMS", 1):
                preview = archive_preview(path, ".zip")

        self.assertEqual(3, preview["entry_count"])
        self.assertTrue(preview["truncated"])
        self.assertEqual(["one.txt"], [row["name"] for row in preview["items"]])

    def test_zip_entry_and_expanded_size_limits_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sample.zip"
            path.write_bytes(_zip_bytes([("one.txt", b"123"), ("two.txt", b"456")]))
            with patch("app.archive_preview.MAX_ARCHIVE_ENTRIES", 1):
                too_many = archive_preview(path, ".zip")
            with patch("app.archive_preview.MAX_ARCHIVE_TOTAL_UNCOMPRESSED_BYTES", 2):
                too_large = archive_preview(path, ".zip")

        self.assertEqual(("too_large", "entry_limit"), (too_many["status"], too_many["reason"]))
        self.assertEqual(("too_large", "expanded_size_limit"), (too_large["status"], too_large["reason"]))

    def test_archive_size_invalid_input_and_unsupported_suffix_are_handled(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            oversized = root / "large.zip"
            oversized.write_bytes(b"12345")
            invalid = root / "broken.zip"
            invalid.write_bytes(b"not a zip")
            with patch("app.archive_preview.MAX_ARCHIVE_PREVIEW_BYTES", 4):
                too_large = archive_preview(oversized, ".zip")
            malformed = archive_preview(invalid, ".zip")
            unsupported = archive_preview(root / "missing.rar", ".rar")

        self.assertEqual("too_large", too_large["status"])
        self.assertEqual("unavailable", malformed["status"])
        self.assertEqual("unsupported_format", unsupported["reason"])

    def test_epub_preview_returns_bounded_reader_metadata_and_chapters(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "book.epub"
            path.write_bytes(_epub_bytes())

            preview = archive_preview(path, ".EPUB")

        self.assertEqual("ready", preview["status"])
        self.assertEqual("Archive sample", preview["title"])
        self.assertEqual("Preview author", preview["author"])
        self.assertEqual(1, preview["chapter_count"])
        self.assertEqual("chapter", preview["items"][0]["kind"])

    def test_epub_with_unsafe_member_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "unsafe.epub"
            path.write_bytes(_epub_bytes(unsafe=True))

            preview = archive_preview(path, ".epub")

        self.assertEqual("unavailable", preview["status"])

    def test_cbz_uses_the_same_bounded_zip_listing(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "comic.cbz"
            path.write_bytes(_zip_bytes([("page-001.jpg", b"not decoded")]))

            preview = archive_preview(path, ".cbz")

        self.assertEqual("ready", preview["status"])
        self.assertEqual("CBZ", preview["format"])
        self.assertEqual(["page-001.jpg"], [item["name"] for item in preview["items"]])
        self.assertIn(".cbz", SUPPORTED_SUFFIXES)

    def test_archive_cache_is_regenerated_if_metadata_is_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = PreviewService(root)
            thumbnail = root / ".webcache" / "doc" / "digest" / "thumbnail.webp"
            thumbnail.parent.mkdir(parents=True)
            thumbnail.write_bytes(b"cached thumbnail")
            metadata = {
                "document_id": "doc",
                "sha256": "digest",
                "last_path": "sample.zip",
                "preview": {
                    "status": "ready",
                    "source_sha256": "digest",
                    "thumbnail": str(thumbnail.relative_to(root)),
                },
            }

            self.assertFalse(service.is_current(metadata))
            metadata["preview"]["archive"] = {
                "status": "unavailable",
                "reason": "read_failed",
            }
            self.assertFalse(service.is_current(metadata))
            metadata["preview"]["archive"] = {
                "status": "unavailable",
                "reason": "invalid_archive",
            }
            self.assertTrue(service.is_current(metadata))

    def test_preview_generation_caches_archive_metadata_without_changing_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sample.zip"
            source.write_bytes(_zip_bytes([("report.txt", b"report data")]))
            original = source.read_bytes()
            digest = hashlib.sha256(original).hexdigest()
            settings_store = SettingsStore(root)
            settings = settings_store.settings()
            settings["media"]["preview_enabled"] = True
            settings_store.save(settings, "archive-preview-test")
            metadata = {
                "document_id": "archive-doc",
                "sha256": digest,
                "last_path": source.name,
            }

            preview = PreviewService(root).generate(source, metadata)

            self.assertEqual("ready", preview["status"])
            self.assertEqual("ready", preview["archive"]["status"])
            self.assertEqual(original, source.read_bytes())


if __name__ == "__main__":
    unittest.main()
