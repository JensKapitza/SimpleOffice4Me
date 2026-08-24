import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.document_store import DocumentStore, PREVIEW_CACHE_DIR
from app.preview_service import PreviewService, detect_preview_tools


class PreviewServiceTest(unittest.TestCase):
    def test_detects_optional_commands_without_executing_them(self):
        found = {"magick": "/usr/bin/magick", "pdftoppm": "/usr/bin/pdftoppm"}
        with patch("app.preview_service.shutil.which", side_effect=lambda name: found.get(name)):
            tools = detect_preview_tools()
        self.assertTrue(tools["commands"]["imagemagick"])
        self.assertTrue(tools["commands"]["pdftoppm"])
        self.assertFalse(tools["commands"]["libreoffice"])
        self.assertFalse(tools["supported"]["office"])

    def test_image_thumbnail_is_cached_and_original_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "large.png"
            Image.new("RGB", (2400, 1600), "red").save(source)
            original = source.read_bytes()
            store = DocumentStore(root)
            store.scan()
            metadata = store.get_document(source)
            service = PreviewService(root)

            preview = service.generate(source, metadata)
            store.set_preview_metadata(metadata["document_id"], preview)

            self.assertEqual("ready", preview["status"])
            self.assertEqual(original, source.read_bytes())
            cached = service.cached_path(store.get_document(metadata["document_id"]))
            self.assertIsNotNone(cached)
            with Image.open(cached) as image:
                self.assertLessEqual(max(image.size), 1200)

    def test_cache_is_not_scanned_and_survives_a_move(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "one.png"
            Image.new("RGB", (20, 20), "blue").save(source)
            store = DocumentStore(root)
            store.scan()
            metadata = store.get_document(source)
            service = PreviewService(root)
            store.set_preview_metadata(metadata["document_id"], service.generate(source, metadata))
            source.rename(root / "two.png")

            report = store.scan()
            moved = store.get_document(root / "two.png")

            self.assertEqual(1, report.files)
            self.assertEqual(metadata["document_id"], moved["document_id"])
            self.assertIsNotNone(service.cached_path(moved))
            self.assertTrue((root / PREVIEW_CACHE_DIR).is_dir())

    def test_external_converter_is_bounded_and_uses_no_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "movie.mp4"
            source.write_bytes(b"not a real movie")
            tools = {"paths": {"ffmpeg": "/usr/bin/ffmpeg", "pdftoppm": None, "libreoffice": None, "imagemagick": None}}
            service = PreviewService(root, tools)
            metadata = {"document_id": "d1", "sha256": "a" * 64}
            with patch("app.preview_service.subprocess.run") as run:
                run.return_value.returncode = 1
                result = service.generate(source, metadata)
            self.assertEqual("failed", result["status"])
            self.assertEqual("conversion_failed", result["error"])
            self.assertNotIn("not a real movie", str(result))
            self.assertNotIn("shell", run.call_args.kwargs)
            self.assertEqual(service.timeout, run.call_args.kwargs["timeout"])

    def test_cache_path_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outside = root / "outside.webp"
            outside.write_bytes(b"x")
            metadata = {"sha256": "x", "preview": {"status": "ready", "source_sha256": "x", "thumbnail": "outside.webp"}}
            self.assertIsNone(PreviewService(root).cached_path(metadata))


if __name__ == "__main__":
    unittest.main()
