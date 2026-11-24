"""Path-traversal regression tests for generated business documents (#1154).

`contact_id` is persisted caller input (contact form, MCP upsert_contact,
vCard/JSContact uid) and reaches the filesystem in `_store_generated_pdf`.
The sink must prove containment before creating anything.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.business_document_generation import _store_generated_pdf


class ContactIdTraversalTests(unittest.TestCase):
    def test_dotdot_contact_id_is_rejected_without_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            root.mkdir()
            escape = Path(temp) / "escape-target"
            with self.assertRaises(ValueError):
                _store_generated_pdf(
                    root, "../../../../escape-target", "Invoice", b"%PDF-1.4",
                    "tester", "invoice", "tpl-1",
                )
            self.assertFalse(escape.exists())
            self.assertFalse((root / "generated").exists())

    def test_absolute_contact_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            root.mkdir()
            with self.assertRaises(ValueError):
                _store_generated_pdf(
                    root, "/tmp/elsewhere", "Invoice", b"%PDF-1.4",
                    "tester", "invoice", "tpl-1",
                )
            self.assertFalse((root / "generated").exists())

    def test_deeply_nested_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            root.mkdir()
            with self.assertRaises(ValueError):
                _store_generated_pdf(
                    root, "ok/../../../../../escape-deep", "Invoice", b"%PDF-1.4",
                    "tester", "invoice", "tpl-1",
                )
            self.assertFalse((Path(temp) / "escape-deep").exists())


if __name__ == "__main__":
    unittest.main()
