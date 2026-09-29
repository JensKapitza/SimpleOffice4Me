from __future__ import annotations
import tempfile
import unittest
from pathlib import Path

from app.v3_inbox import InboxStore


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


if __name__=="__main__":
    unittest.main()
