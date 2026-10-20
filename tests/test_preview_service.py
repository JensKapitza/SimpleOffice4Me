import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.document_store import DocumentStore, PREVIEW_CACHE_DIR
from app.preview_service import PreviewService, detect_preview_tools
from app.settings_store import SettingsStore


class PreviewServiceTest(unittest.TestCase):
    def test_central_media_settings_bound_preview_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            SettingsStore(root).save(
                {
                    "media": {
                        "preview_enabled": False,
                        "preview_timeout_seconds": 30,
                        "preview_max_bytes": 2 * 1024 * 1024,
                        "preview_max_pixels": 2_000_000,
                        "video_preview_frames": 12,
                    },
                },
                "media-admin",
            )
            service = PreviewService(root, {"paths": {"ffmpeg": None}})

            self.assertFalse(service.preview_enabled)
            self.assertEqual(30, service.timeout)
            self.assertEqual(2 * 1024 * 1024, service.max_bytes)
            self.assertEqual(2_000_000, service.max_pixels)
            self.assertEqual(12, service.video_frame_count)
            self.assertFalse(service.supports(root / "image.jpg"))

    def test_live_index_service_refreshes_media_setting_after_settings_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("app.preview_service.time.monotonic", side_effect=(0.0, 0.0, 2.0)):
                service = PreviewService(root, {"paths": {"ffmpeg": None}})
                image = root / "image.jpg"
                self.assertTrue(service.supports(image))

                SettingsStore(root).save(
                    {"media": {
                        "preview_enabled": False,
                        "preview_timeout_seconds": 30,
                        "preview_max_bytes": 2 * 1024 * 1024,
                        "preview_max_pixels": 2_000_000,
                        "video_preview_frames": 8,
                    }},
                    "media-admin",
                )

                self.assertFalse(service.supports(image))
                self.assertEqual(30, service.timeout)
                self.assertEqual(8, service.video_frame_count)

    def test_legacy_preview_environment_is_preserved_until_settings_migration(self):
        environment = {
            "SIMPLEOFFICE_PREVIEWS": "off",
            "SIMPLEOFFICE_PREVIEW_TIMEOUT_SECONDS": "75",
            "SIMPLEOFFICE_PREVIEW_MAX_BYTES": "2048",
            "SIMPLEOFFICE_PREVIEW_MAX_PIXELS": "2000000",
            "SIMPLEOFFICE_VIDEO_PREVIEW_FRAMES": "6",
        }
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, environment):
            root = Path(temp)
            legacy = PreviewService(root, {"paths": {"ffmpeg": None}})
            self.assertFalse(legacy.preview_enabled)
            self.assertEqual(75, legacy.timeout)
            self.assertEqual(2048, legacy.max_bytes)
            self.assertEqual(2_000_000, legacy.max_pixels)
            self.assertEqual(6, legacy.video_frame_count)

            SettingsStore(root).save({"interface": {"timezone": "UTC"}}, "media-admin")
            migrated = PreviewService(root, {"paths": {"ffmpeg": None}})
            self.assertFalse(migrated.preview_enabled)
            self.assertEqual(75, migrated.timeout)
            self.assertEqual(2048, migrated.max_bytes)
            self.assertEqual(2_000_000, migrated.max_pixels)
            self.assertEqual(6, migrated.video_frame_count)

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
