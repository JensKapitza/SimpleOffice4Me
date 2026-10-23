import io
import tempfile
import unittest
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g, session
from PIL import Image

from app.document_store import CONTROL_DIR, DocumentStore
from app.photo_upload import PhotoBulkImporter, bp as photo_upload_blueprint, metadata_tags
from app.security_controls import protect_browser_mutation


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
                imported = importer.import_photo(
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

            document = imported.document
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

    def test_untrusted_upload_path_is_reduced_to_a_safe_filename(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = PhotoBulkImporter(root).import_photo(
                self._jpeg(), "../../outside/camera.jpg", "tester"
            )

            self.assertEqual("inbox/camera.jpg", result.document["last_path"])
            self.assertTrue((root / result.document["last_path"]).is_file())

    def test_received_at_is_a_real_utc_instant(self):
        with tempfile.TemporaryDirectory() as temp:
            document = PhotoBulkImporter(temp).import_photo(self._jpeg(), "photo.jpg", "tester").document
            received = datetime.fromisoformat(document["attributes"]["photo_upload"]["received_at"].replace("Z", "+00:00"))
            if received.tzinfo is None:
                received = received.replace(tzinfo=timezone.utc)
            self.assertLess(abs((datetime.now(timezone.utc) - received.astimezone(timezone.utc)).total_seconds()), 30)

    def test_repeated_photo_content_reuses_the_existing_document(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            importer = PhotoBulkImporter(root)
            payload = self._jpeg().getvalue()
            first = importer.import_photo(io.BytesIO(payload), "camera-1.jpg", "tester")
            retry = importer.import_photo(io.BytesIO(payload), "renamed-retry.jpg", "tester")

            self.assertFalse(first.duplicate)
            self.assertTrue(retry.duplicate)
            self.assertEqual(first.document["document_id"], retry.document["document_id"])
            self.assertEqual("camera-1.jpg", retry.document["attributes"]["photo_upload"]["original_filename"])
            self.assertEqual(1, len(list((root / "inbox").glob("*.jpg"))))

    def test_concurrent_identical_photo_uploads_create_only_one_document(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            importer = PhotoBulkImporter(root)
            payload = self._jpeg().getvalue()

            def import_copy(index):
                return importer.import_photo(io.BytesIO(payload), f"camera-{index}.jpg", "tester")

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(import_copy, (1, 2)))

            self.assertEqual(1, len({result.document["document_id"] for result in results}))
            self.assertEqual(1, sum(not result.duplicate for result in results))
            self.assertEqual(1, len(list((root / "inbox").glob("*.jpg"))))

    def test_concurrent_same_name_different_photos_never_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            importer = PhotoBulkImporter(root)
            payloads = (self._jpeg(80, 40), self._jpeg(40, 80))

            def import_copy(payload):
                return importer.import_photo(payload, "camera.jpg", "tester")

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(import_copy, payloads))

            self.assertEqual(2, len({result.document["document_id"] for result in results}))
            self.assertEqual(2, len(list((root / "inbox").glob("*.jpg"))))

    def test_archive_choice_keeps_inbox_and_archive_destinations_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            importer = PhotoBulkImporter(root)
            payload = self._jpeg().getvalue()
            inbox = importer.import_photo(io.BytesIO(payload), "same.jpg", "tester")
            archived = importer.import_photo(io.BytesIO(payload), "same.jpg", "tester", archive=True)

            self.assertFalse(inbox.duplicate)
            self.assertFalse(archived.duplicate)
            self.assertNotEqual(inbox.document["document_id"], archived.document["document_id"])
            self.assertEqual(1, len(list((root / "inbox").glob("*.jpg"))))
            self.assertEqual(1, len(list((root / "archive").rglob("*.jpg"))))

    def test_retry_finishes_metadata_for_file_left_by_interrupted_import(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            importer = PhotoBulkImporter(root)
            importer.store.initialize()
            folder = root / "inbox"
            folder.mkdir()
            payload = self._jpeg().getvalue()
            (folder / "camera.jpg").write_bytes(payload)

            result = importer.import_photo(io.BytesIO(payload), "camera.jpg", "tester")

            self.assertFalse(result.duplicate)
            self.assertEqual("camera.jpg", result.document["attributes"]["photo_upload"]["original_filename"])
            self.assertEqual(1, len(list(folder.glob("*.jpg"))))

    def test_retry_completes_metadata_after_post_scan_processing_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            importer = PhotoBulkImporter(root)
            payload = self._jpeg().getvalue()
            with patch("app.photo_upload.extract_photo_metadata", side_effect=OSError("metadata tool unavailable")):
                with self.assertRaisesRegex(OSError, "metadata tool unavailable"):
                    importer.import_photo(io.BytesIO(payload), "camera.jpg", "tester")

            target = root / "inbox" / "camera.jpg"
            partial = importer.store.get_document(target)
            self.assertNotIn("photo_upload", partial.get("attributes", {}))

            resumed = importer.import_photo(io.BytesIO(payload), "camera.jpg", "tester")

            self.assertFalse(resumed.duplicate)
            self.assertEqual(partial["document_id"], resumed.document["document_id"])
            self.assertTrue(resumed.document["attributes"]["photo_upload"]["sha256"])
            self.assertEqual(1, len(list((root / "inbox").glob("*.jpg"))))


class PhotoUploadHttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            DOCUMENT_ROOT=self.temp.name,
            SECRET_KEY="photo-upload-http-test",
            MAX_CONTENT_LENGTH=1024 * 1024,
        )

        @self.app.before_request
        def authenticate_test_user():
            username = session.get("username")
            g.user = {"username": username, "is_admin": True} if username else None

        self.app.add_url_rule("/auth/login", endpoint="auth.login", view_func=lambda: "login")
        self.app.add_url_rule(
            "/documents/<document_id>",
            endpoint="documents.detail",
            view_func=lambda _document_id: "document-detail",
        )
        self.app.before_request(protect_browser_mutation)
        self.app.register_blueprint(photo_upload_blueprint)
        self.client = self.app.test_client()
        with self.client.session_transaction() as state:
            state["username"] = "tester"
            state["_csrf_token"] = "photo-upload-http-csrf-token"

    @staticmethod
    def _jpeg():
        stream = io.BytesIO()
        Image.new("RGB", (80, 40), "white").save(stream, format="JPEG")
        return stream.getvalue()

    def _post(self, filename: str, payload: bytes):
        return self.client.post(
            "/documents/photos/upload",
            data={
                "_csrf_token": "photo-upload-http-csrf-token",
                "file": (io.BytesIO(payload), filename),
                "batch_id": f"batch-{filename}",
            },
        )

    def test_duplicate_photo_response_reuses_existing_document(self):
        payload = self._jpeg()
        first = self._post("camera.jpg", payload)
        duplicate = self._post("camera-retry.jpg", payload)

        self.assertEqual(200, first.status_code)
        self.assertFalse(first.json["duplicate"])
        self.assertEqual(200, duplicate.status_code)
        self.assertTrue(duplicate.json["duplicate"])
        self.assertEqual(first.json["document_id"], duplicate.json["document_id"])

    def test_photo_upload_records_a_completed_item_through_the_optional_web_inbox(self):
        payload = self._jpeg()
        inbox = {"item": object(), "error": "", "malware_status": "clean"}
        with patch("app.photo_upload.begin_web_inbox_upload", return_value=inbox) as begin, \
             patch("app.photo_upload.complete_web_inbox_upload") as complete:
            response = self._post("inbox-photo.jpg", payload)

        self.assertEqual(200, response.status_code)
        self.assertEqual(4, len(begin.call_args.args))
        self.assertEqual(inbox, complete.call_args.args[1])
        self.assertEqual(response.json["document_id"], complete.call_args.kwargs["document_id"])

    def test_photo_upload_scan_rejection_stops_before_document_storage(self):
        inbox = {
            "item": object(),
            "error": "Malware-Scanner nicht verfügbar.",
            "error_status": 503,
        }
        with patch("app.photo_upload.begin_web_inbox_upload", return_value=inbox), \
             patch("app.photo_upload.PhotoBulkImporter.import_photo") as importer:
            response = self._post("blocked-photo.jpg", self._jpeg())

        self.assertEqual(503, response.status_code)
        importer.assert_not_called()
        self.assertEqual("Malware-Scanner nicht verfügbar.", response.json["error"])

    def test_unsupported_heic_photo_has_a_clear_generic_import_fallback(self):
        response = self.client.post(
            "/documents/photos/upload",
            data={
                "_csrf_token": "photo-upload-http-csrf-token",
                "file": (io.BytesIO(b"not decoded by the photo path"), "photo.heic"),
            },
        )

        self.assertEqual(415, response.status_code)
        self.assertIn("HEIC kann als normale Datei importiert werden", response.json["error"])


if __name__ == "__main__":
    unittest.main()
