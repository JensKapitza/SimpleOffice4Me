"""Path-traversal regression tests for generated business documents (#1154).

`contact_id` is persisted caller input (contact form, MCP upsert_contact,
vCard/JSContact uid) and reaches the filesystem in `_store_generated_pdf`.
The sink must prove containment before creating anything.
"""
from __future__ import annotations

import io
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from reportlab.pdfgen import canvas

from app.business_document_generation import _store_generated_pdf
from app.business_document_storage import write_generated_pdf
from app.business_documents import contact_links
from app.document_store import CONTROL_DIR


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
            escape = Path(temp) / "elsewhere"
            with self.assertRaises(ValueError):
                _store_generated_pdf(
                    root, str(escape), "Invoice", b"%PDF-1.4",
                    "tester", "invoice", "tpl-1",
                )
            self.assertFalse(escape.exists())
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

    def test_contact_ids_cannot_select_a_sibling_or_control_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for dirname, separator in (("sibling", "/"), (CONTROL_DIR, "\\")):
                with self.subTest(dirname=dirname):
                    sibling = root / dirname
                    sibling.mkdir()
                    invalid_id = separator.join(("..", "..", "..", dirname))
                    with self.assertRaises(ValueError):
                        _store_generated_pdf(
                            root, invalid_id, "Invoice", b"%PDF-1.4",
                            "tester", "invoice", "tpl-1",
                        )
                    self.assertEqual([], list(sibling.iterdir()))
            self.assertFalse((root / "generated").exists())

    def test_generated_path_rejects_directory_links(self):
        for linked_component in ("generated", "invoice", "year", "contact-1"):
            with self.subTest(linked_component=linked_component), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "root"
                root.mkdir()
                year = datetime.now(timezone.utc).strftime("%Y")
                components = ("generated", "invoice", year, "contact-1")
                component = year if linked_component == "year" else linked_component
                link = root.joinpath(*components[:components.index(component) + 1])
                link.parent.mkdir(parents=True, exist_ok=True)
                destination = Path(temp) / "outside-root" if linked_component == "generated" else root / CONTROL_DIR
                destination.mkdir()
                try:
                    link.symlink_to(destination, target_is_directory=True)
                except (NotImplementedError, OSError):
                    self.skipTest("directory symlinks are unavailable")
                with self.assertRaises(ValueError):
                    _store_generated_pdf(
                        root, "contact-1", "Invoice", b"%PDF-1.4",
                        "tester", "invoice", "tpl-1",
                    )
                self.assertEqual([], list(destination.iterdir()))

    def test_generated_file_is_created_exclusively(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = (root, "invoice", "2026", "contact-1", "20260101-Invoice.pdf")
            path = write_generated_pdf(*args, b"existing")
            with self.assertRaises(FileExistsError):
                write_generated_pdf(*args, b"replacement")
            self.assertEqual(b"existing", path.read_bytes())

    def test_legacy_uid_keeps_contact_link_and_uses_one_component(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = io.BytesIO()
            pdf = canvas.Canvas(source)
            pdf.drawString(20, 20, "Invoice")
            pdf.save()
            uid = "urn:uid:Kunde/Grüße"
            document = _store_generated_pdf(
                root, uid, "Invoice", source.getvalue(), "tester", "invoice", "tpl-1",
            )
            relative = Path(document["last_path"])
            self.assertEqual(("generated", "invoice"), relative.parts[:2])
            self.assertEqual(5, len(relative.parts))
            self.assertTrue((root / relative).is_file())
            self.assertEqual(uid, document["attributes"]["contact_id"])
            self.assertEqual(document["document_id"], contact_links(root, uid)[0]["document_id"])


if __name__ == "__main__":
    unittest.main()
