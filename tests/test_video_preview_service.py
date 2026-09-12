import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.preview_service import PreviewService
from app.video_settings import save_video_preview_frame_count, video_preview_frame_count


class VideoPreviewServiceTest(unittest.TestCase):
    @staticmethod
    def _tools():
        return {
            "paths": {
                "ffmpeg": "/fake/ffmpeg",
                "ffprobe": "/fake/ffprobe",
                "pdftoppm": None,
                "libreoffice": None,
                "imagemagick": None,
            }
        }

    def test_ten_frames_are_normalized_over_complete_duration(self):
        positions = PreviewService.video_frame_positions(100.0, 10)
        self.assertEqual(10, len(positions))
        self.assertEqual(5.0, positions[0])
        self.assertEqual(95.0, positions[-1])
        self.assertEqual([10.0] * 9, [positions[index + 1] - positions[index] for index in range(9)])

    def test_configurable_frame_count_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {"SIMPLEOFFICE_VIDEO_PREVIEW_FRAMES": "18"}):
                self.assertEqual(18, PreviewService(temp, self._tools()).video_frame_count)
            with patch.dict(os.environ, {"SIMPLEOFFICE_VIDEO_PREVIEW_FRAMES": "999"}):
                self.assertEqual(30, PreviewService(temp, self._tools()).video_frame_count)

    def test_saved_frame_count_overrides_deployment_default(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.dict(os.environ, {"SIMPLEOFFICE_VIDEO_PREVIEW_FRAMES": "18"}):
                self.assertEqual(18, video_preview_frame_count(root))
                self.assertEqual(7, save_video_preview_frame_count(root, 7, "tester"))
                self.assertEqual(7, video_preview_frame_count(root))
                self.assertEqual(7, PreviewService(root, self._tools()).video_frame_count)

    def test_generate_publishes_ten_frames_and_video_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "movie.mp4"
            source.write_bytes(b"video-placeholder")
            metadata = {
                "document_id": "video-1",
                "sha256": "a" * 64,
                "last_path": "movie.mp4",
            }
            service = PreviewService(root, self._tools())

            def fake_frame(_source, destination, _timestamp):
                Image.new("RGB", (320, 180), "navy").save(destination, "WEBP")

            with patch.object(service, "_probe_video", return_value={
                "duration_seconds": 100.0,
                "codec": "h264",
                "width": 1920,
                "height": 1080,
                "container": "mp4",
            }), patch.object(service, "_extract_video_frame", side_effect=fake_frame):
                preview = service.generate(source, metadata)

            self.assertEqual("ready", preview["status"])
            video = preview["video"]
            self.assertEqual(10, video["frame_target_count"])
            self.assertEqual(10, video["frame_count"])
            self.assertEqual(10.0, video["frame_interval_seconds"])
            self.assertTrue(video["normalized_timeline"])
            self.assertEqual(5.0, video["frames"][0]["timestamp_seconds"])
            self.assertEqual(95.0, video["frames"][-1]["timestamp_seconds"])
            self.assertTrue((root / preview["thumbnail"]).is_file())
            self.assertTrue((root / preview["collage"]).is_file())
            for index in range(10):
                self.assertIsNotNone(service.cached_video_frame({**metadata, "preview": preview}, index))

    def test_changed_frame_count_marks_video_preview_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cached = root / ".webcache" / "v" / ("a" * 64)
            cached.mkdir(parents=True)
            (cached / "thumbnail.webp").write_bytes(b"x")
            frame = cached / "frames" / "video-frame-001.webp"
            frame.parent.mkdir()
            frame.write_bytes(b"x")
            metadata = {
                "document_id": "v",
                "sha256": "a" * 64,
                "last_path": "movie.mp4",
                "preview": {
                    "status": "ready",
                    "source_sha256": "a" * 64,
                    "thumbnail": str((cached / "thumbnail.webp").relative_to(root)),
                    "video": {
                        "frame_target_count": 10,
                        "frames": [{"index": 0, "path": str(frame.relative_to(root))}],
                    },
                },
            }
            with patch.dict(os.environ, {"SIMPLEOFFICE_VIDEO_PREVIEW_FRAMES": "12"}):
                service = PreviewService(root, self._tools())
                self.assertFalse(service.is_current(metadata))
                self.assertIsNone(service.cached_path(metadata))

    def test_transcode_is_a_cache_variant_not_a_new_document_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "original.mkv"
            source.write_bytes(b"original")
            metadata = {
                "document_id": "video-2",
                "sha256": "b" * 64,
                "last_path": "original.mkv",
            }
            service = PreviewService(root, self._tools())

            def fake_run(command):
                Path(command[-1]).write_bytes(b"transcoded")

            with patch.object(service, "_run", side_effect=fake_run):
                variant = service.transcode_video(source, metadata, "tester")

            target = root / variant["path"]
            self.assertTrue(target.is_file())
            self.assertIn(".webcache", target.parts)
            self.assertEqual("b" * 64, variant["derived_from_sha256"])
            self.assertEqual(b"original", source.read_bytes())
            self.assertIsNotNone(service.cached_video_variant({
                **metadata,
                "preview": {
                    "status": "ready",
                    "source_sha256": "b" * 64,
                    "video": {"variants": [variant]},
                },
            }, "h264-720p"))

    def test_cached_variant_is_rejected_after_original_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cached = root / ".webcache" / "v" / ("a" * 64) / "variants" / "h264-720p.mp4"
            cached.parent.mkdir(parents=True)
            cached.write_bytes(b"variant")
            metadata = {
                "document_id": "v",
                "sha256": "b" * 64,
                "last_path": "movie.mp4",
                "preview": {
                    "status": "ready",
                    "source_sha256": "a" * 64,
                    "video": {"variants": [{"variant_id": "h264-720p", "path": str(cached.relative_to(root))}]},
                },
            }
            self.assertIsNone(PreviewService(root, self._tools()).cached_video_variant(metadata, "h264-720p"))


if __name__ == "__main__":
    unittest.main()
