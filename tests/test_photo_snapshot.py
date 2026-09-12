import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.photo_upload import PhotoBulkImporter


class PhotoSnapshotTest(unittest.TestCase):
    @staticmethod
    def _jpeg():
        stream = io.BytesIO()
        Image.new("RGB", (1280, 720), "white").save(stream, format="JPEG")
        stream.seek(0)
        return stream

    def test_direct_camera_provenance_is_persisted(self):
        with tempfile.TemporaryDirectory() as temp:
            document = PhotoBulkImporter(Path(temp)).import_photo(
                self._jpeg(),
                "snapshot.jpg",
                "tester",
                client={
                    "mime_type": "image/jpeg",
                    "size": 12345,
                    "last_modified_at": "2026-09-12T09:15:00.000Z",
                    "timezone": "Europe/Berlin",
                    "timezone_offset_minutes": -120,
                    "relative_path": "camera/direct",
                    "batch_id": "snapshot-1",
                    "batch_index": 1,
                    "batch_count": 1,
                    "user_agent": "Snapshot Browser",
                },
            )
            upload = document["attributes"]["photo_upload"]
            rich = document["attributes"]["photo_metadata"]["derived"]
            self.assertEqual("camera/direct", upload["client_relative_path"])
            self.assertEqual("Europe/Berlin", upload["client_timezone"])
            self.assertEqual("Snapshot Browser", upload["user_agent"])
            self.assertEqual("2026-09-12T09:15:00.000Z", upload["client_last_modified_at"])
            self.assertEqual(1280, rich["width"])
            self.assertEqual(720, rich["height"])
            self.assertTrue(upload["sha256"])

    def test_snapshot_template_requests_environment_camera(self):
        template = Path("templates/documents/_photo_snapshot.html").read_text(encoding="utf-8")
        self.assertIn('capture="environment"', template)
        self.assertIn('name="client_relative_path" value="camera/direct"', template)
        self.assertIn('name="client_timezone"', template)
        self.assertIn('name="client_last_modified_at"', template)


if __name__ == "__main__":
    unittest.main()
