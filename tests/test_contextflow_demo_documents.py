"""Demo documents: synthetic imports with eager previews, idempotent."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.contextflow.demo import DEMO_DOCUMENT_TAG, seed_demo_documents
from app.document_store import DocumentStore
from app.preview_service import PreviewService


class DemoDocumentsTests(unittest.TestCase):
    def test_seed_imports_four_files_with_ready_previews(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "documents"
            result = seed_demo_documents(root, "demo")
            self.assertTrue(result["synthetic"])
            self.assertEqual(4, len(result["documents"]))
            names = sorted(entry["name"] for entry in result["documents"])
            self.assertEqual(
                ["demo-bericht.md", "demo-foto.png", "demo-notizen.txt", "demo-seite.pdf"],
                names,
            )
            store = DocumentStore(root)
            service = PreviewService(root)
            for entry in result["documents"]:
                self.assertEqual("ready", entry["preview"])
                metadata = store.get_document(entry["document_id"])
                self.assertIn(DEMO_DOCUMENT_TAG, metadata.get("tags", []))
                self.assertIsNotNone(service.cached_path(metadata, "thumbnail"))
            png = next(e for e in result["documents"] if e["name"] == "demo-foto.png")
            self.assertEqual("pillow", png["converter"])

    def test_reseed_reuses_documents_without_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "documents"
            first = seed_demo_documents(root, "demo")
            second = seed_demo_documents(root, "demo")
            first_ids = sorted(e["document_id"] for e in first["documents"])
            second_ids = sorted(e["document_id"] for e in second["documents"])
            self.assertEqual(first_ids, second_ids)
            store = DocumentStore(root)
            store.scan()
            tagged = [
                doc
                for doc in (store.get_document(e) for e in first_ids)
                if DEMO_DOCUMENT_TAG in doc.get("tags", [])
            ]
            self.assertEqual(4, len(tagged))


if __name__ == "__main__":
    unittest.main()
