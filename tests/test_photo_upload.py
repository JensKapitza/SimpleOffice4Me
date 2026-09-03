import io
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.document_store import CONTROL_DIR, DocumentStore
from app.photo_upload import PhotoBulkImporter, metadata_tags


class PhotoBulkUploadTest(unittest.TestCase):
    @staticmethod
    def _jpeg(width=80, height=40):
        stream = io.BytesIO()
        Image.new("RGB", (width, height), "white").save(stream, format="JPEG")
        stream.seek(0)
        return stream

    def test_bulk_import_records_server_and_client_metadata_without_full_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            importer = PhotoBulkImporter(root)
            client_last_modified = "2026-08-31T14:15:16.000Z"
            with patch.object(DocumentStore, "scan", side_effect=AssertionError("bulk upload must not rescan archive")):
                document = importer.import_photo(
                    self._jpeg(),
                    "DCIM_0001.jpg",
                    "tester",
                    client={
                        "mime_type": "image/jpeg",
                        "size": 687,
                        "last_modified_at": client_last_modified,
                        "timezone": "Europe/Berlin",
                        "timezone_offset_minutes": -120,
                        "relative_path": "DCIM/Camera/DCIM_0001.jpg",
                        "batch_id": "test-batch",
                        "batch_index": 1,
                        "batch_count": 20,
                        "user_agent": "Mobile Test",
                    },
                )

            upload = document["attributes"]["photo_upload"]
            rich = document["attributes"]["photo_metadata"]
            self.assertEqual("mobile-web-bulk", upload["source"])
            self.assertEqual(client_last_modified, upload["client_last_modified_at"])
            self.assertEqual("test-batch", upload["batch_id"])
            self.assertEqual(20, upload["batch_count"])
            self.assertTrue(upload["received_at"])
            self.assertEqual("JPEG", rich["derived"]["format"])
            self.assertEqual(80, rich["derived"]["width"])
            self.assertEqual(40, rich["derived"]["height"])
            self.assertIn("foto-upload", document["tags"])
            self.assertIn("datei-2026-08-31", document["tags"])
            self.assertIn("querformat", document["tags"])
            self.assertTrue(any(tag.startswith("upload-") for tag in document["tags"]))
            self.assertEqual("deferred_bulk_upload", document["image_analysis"]["ocr_status"])
            self.assertTrue((root / document["last_path"]).is_file())

    def test_metadata_tags_keep_gps_coordinates_out_of_tag_names(self):
        tags = metadata_tags(
            received_at="2026-09-03T19:30:00+00:00",
            timezone_name="Europe/Berlin",
            client_last_modified_at="2026-09-01T10:00:00+00:00",
            rich_metadata={
                "derived": {
                    "captured_at": "2026:08:30 12:34:56",
                    "format": "JPEG",
                    "width": 4000,
                    "height": 3000,
                    "camera_make": "Samsung",
                    "camera_model": "SM-S928B",
                    "lens_model": "Main Camera",
                    "gps": {"latitude": 51.4344, "longitude": 6.7623},
                },
                "pillow": {"format": "JPEG"},
                "exiftool": {"status": "completed"},
            },
        )
        self.assertIn("upload-2026-09-03", tags)
        self.assertIn("datei-2026-09-01", tags)
        self.assertIn("aufnahme-2026-08-30", tags)
        self.assertIn("gps", tags)
        self.assertIn("kamera-samsung-sm-s928b", tags)
        self.assertIn("objektiv-main-camera", tags)
        joined = " ".join(tags)
        self.assertNotIn("51.4344", joined)
        self.assertNotIn("6.7623", joined)

    def test_invalid_photo_is_rejected_and_staging_is_clean(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            importer = PhotoBulkImporter(root)
            with self.assertRaisesRegex(ValueError, "Erlaubt"):
                importer.import_photo(io.BytesIO(b"not an image"), "payload.txt", "tester")
            staging = root / CONTROL_DIR / "staging"
            self.assertEqual([], list(staging.glob("*")) if staging.exists() else [])

    def test_received_at_is_a_real_utc_instant(self):
        with tempfile.TemporaryDirectory() as temp:
            document = PhotoBulkImporter(temp).import_photo(self._jpeg(), "photo.jpg", "tester")
            received = datetime.fromisoformat(document["attributes"]["photo_upload"]["received_at"].replace("Z", "+00:00"))
            if received.tzinfo is None:
                received = received.replace(tzinfo=timezone.utc)
            self.assertLess(abs((datetime.now(timezone.utc) - received.astimezone(timezone.utc)).total_seconds()), 30)


if __name__ == "__main__":
    unittest.main()
