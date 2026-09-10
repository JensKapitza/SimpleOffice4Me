import json
import tempfile
import unittest
from pathlib import Path

from app.document_store import DocumentStore


class DocumentPathBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.file = self.root / "hello.txt"
        self.file.write_text("hello", encoding="utf-8")
        self.store = DocumentStore(self.root)
        self.store.scan()
        self.document = self.store.get_document("hello.txt")

    def tearDown(self):
        self.temp.cleanup()

    def test_relative_and_absolute_internal_file_references_remain_supported(self):
        self.assertEqual("hello.txt", self.store.get_document("hello.txt")["last_path"])
        self.assertEqual(
            self.document["document_id"],
            self.store.get_document(self.file.resolve())["document_id"],
        )

    def test_traversal_document_references_are_rejected(self):
        for value in ("../../etc/passwd", r"..\..\secret.txt", "./../secret.txt"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.get_document(value)

    def test_absolute_external_file_reference_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            outside_file = Path(outside) / "outside.txt"
            outside_file.write_text("secret", encoding="utf-8")
            with self.assertRaises(ValueError):
                self.store.get_document(outside_file)

    def test_tampered_last_path_is_rejected_before_caller_file_io(self):
        document_id = self.document["document_id"]
        metadata_path = self.store.documents / f"{document_id}.json"
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        payload["last_path"] = "../../outside.txt"
        metadata_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.get_document(document_id)


if __name__ == "__main__":
    unittest.main()
