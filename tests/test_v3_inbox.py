from __future__ import annotations
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.attachment_security import QuarantineCapacityError
from app.v3_inbox import InboxStore
from app.v3_inbox_web import begin_web_inbox_upload


class V3InboxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.store=InboxStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_same_source_key_is_idempotent(self):
        first=self.store.begin("web","req:file","a.txt","alice")
        second=self.store.begin("web","req:file","a.txt","alice")
        self.assertEqual(first.item_id,second.item_id)

    def test_completed_import_keeps_provenance_and_steps(self):
        item=self.store.begin("s3","inbox/a.txt","a.txt","alice")
        done=self.store.complete_import(
            item.item_id,
            document_id="doc-1",
            sha256="a"*64,
            size=12,
            malware_status="clean",
        )
        self.assertEqual("accepted",done.status)
        self.assertEqual("s3",done.source)
        steps=[row["step"] for row in self.store.steps(item.item_id)]
        self.assertIn("malware_check",steps)
        self.assertIn("persist_import",steps)

    def test_quarantine_has_no_document(self):
        item=self.store.begin("web","req:bad","bad.bin","alice")
        quarantined=self.store.transition(
            item.item_id,
            "quarantined",
            step="malware_check",
            detail="blocked",
        )
        self.assertEqual("",quarantined.document_id)

    def test_filters_are_bounded(self):
        self.store.begin("web","1","a.txt","alice")
        self.store.begin("s3","2","b.txt","alice")
        self.assertEqual(1,len(self.store.list(source="s3",limit=9999)))

    def test_web_upload_scan_failures_report_service_unavailable(self):
        class Uploaded:
            filename = "photo.jpg"
            mimetype = "image/jpeg"

            def __init__(self):
                self.stream = io.BytesIO(b"image bytes")

        for failure in (QuarantineCapacityError(), OSError("scanner unavailable")):
            with self.subTest(failure=type(failure).__name__), patch(
                "app.v3_inbox_web.v3_capability_enabled", return_value=True
            ), patch(
                "app.attachment_security.AttachmentSecurity.scan_webdav_upload",
                side_effect=failure,
            ):
                result = begin_web_inbox_upload({
                    "DOCUMENT_ROOT": str(self.root),
                    "WEBDAV_UPLOAD_SCAN": True,
                    "WEBDAV_QUARANTINE_BYTES": 1024,
                }, Uploaded(), "alice", f"photo-{type(failure).__name__}")

                self.assertEqual(503, result["error_status"])
                self.assertTrue(result["error"])
                saved = self.store.get(result["item"].item_id)
                self.assertEqual("failed", saved.status)


if __name__=="__main__":
    unittest.main()
