from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pypdf import PdfReader

from app.business_template_editor import (
    create_visual_template,
    default_visual_layout,
    save_visual_template,
    visual_template,
)
from app.business_template_render import (
    render_visual_invoice_pdf,
    render_visual_letter_pdf,
    sample_invoice,
)
from app.business_documents import (
    _business_template,
    _business_template_choices,
    _render_invoice_with_template,
    _render_letter_with_template,
)


class VisualBusinessTemplateRenderTests(unittest.TestCase):
    def _create(self, root: Path, document_type: str, name: str = "Visuell") -> dict:
        with mock.patch("app.business_template_editor.DocumentStore"):
            return create_visual_template(
                root, {"name": name, "document_type": document_type}, "tester"
            )

    @staticmethod
    def _text(pdf: bytes) -> str:
        return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)

    def test_default_invoice_layout_contains_dynamic_invoice_components(self):
        layout = default_visual_layout("invoice")
        types = [element["type"] for element in layout["elements"]]
        self.assertEqual(1, types.count("invoice_lines"))
        self.assertEqual(1, types.count("invoice_totals"))
        self.assertNotIn("body", types)

    def test_default_letter_layout_contains_body_component(self):
        layout = default_visual_layout("letter")
        types = [element["type"] for element in layout["elements"]]
        self.assertEqual(1, types.count("body"))
        self.assertNotIn("invoice_lines", types)

    def test_visual_invoice_pdf_contains_template_tokens_lines_and_totals(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            template = self._create(root, "invoice", "Rechnung Blau")
            row = sample_invoice()
            row["seller"]["name"] = "Kapitza Test GmbH"
            row["invoice_number"] = "RE-TEST-42"

            pdf = render_visual_invoice_pdf(root, template["template_id"], row)
            reader = PdfReader(io.BytesIO(pdf))
            text = self._text(pdf)

            self.assertEqual(1, len(reader.pages))
            self.assertIn("Kapitza Test GmbH", text)
            self.assertIn("RE-TEST-42", text)
            self.assertIn("Beratung und Umsetzung", text)
            self.assertIn("392.70", text)

    def test_visual_invoice_splits_long_position_lists_across_pages(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            template = self._create(root, "invoice")
            row = sample_invoice()
            row["lines"] = [
                {
                    "line_id": str(index),
                    "description": f"Leistungsposition {index} mit etwas Beschreibung",
                    "quantity": "1",
                    "net_unit_price": "10.00",
                    "vat_rate": "19.00",
                    "net_total": "10.00",
                }
                for index in range(1, 36)
            ]
            row["totals"] = {"net": "350.00", "tax": "66.50", "gross": "416.50"}

            pdf = render_visual_invoice_pdf(root, template["template_id"], row)
            reader = PdfReader(io.BytesIO(pdf))
            text = self._text(pdf)

            self.assertGreater(len(reader.pages), 1)
            self.assertIn("Leistungsposition 1", text)
            self.assertIn("Leistungsposition 35", text)
            self.assertIn("416.50", text)

    def test_visual_letter_renders_body_and_company_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            template = self._create(root, "letter", "Brief Standard")
            pdf = render_visual_letter_pdf(
                root,
                template["template_id"],
                business={
                    "seller_name": "Kapitza Test GmbH",
                    "seller_street": "Testweg 1",
                    "seller_postal": "47137",
                    "seller_city": "Duisburg",
                    "seller_iban": "DE123",
                    "seller_bic": "TESTBIC",
                    "seller_bank": "Testbank",
                },
                recipient="Max Muster\nKundenstraße 5\n54321 Kundenstadt",
                subject="Testbrief",
                body="Hallo Max,\n\ndies ist der eigentliche Dokumenttext.",
            )
            text = self._text(pdf)
            self.assertIn("Kapitza Test GmbH", text)
            self.assertIn("Max Muster", text)
            self.assertIn("eigentliche Dokumenttext", text)

    def test_visual_template_choices_are_added_without_changing_legacy_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            visual = self._create(root, "invoice", "Visuelle Rechnung")
            legacy = {"template_id": "legacy-1", "name": "Legacy", "active": True}
            with mock.patch("app.business_template_dispatch.templates", return_value=[legacy]):
                choices = _business_template_choices(root, "invoice")

            self.assertIs(legacy, choices[0])
            self.assertEqual(visual["template_id"], choices[1]["template_id"])
            self.assertTrue(choices[1]["visual"])
            self.assertFalse(choices[1]["active"])

    def test_business_template_resolves_visual_and_enforces_document_type(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            visual = self._create(root, "invoice")
            with mock.patch("app.business_template_dispatch.active_template", side_effect=ValueError("legacy missing")):
                resolved = _business_template(root, visual["template_id"], "invoice")
                self.assertEqual(visual["template_id"], resolved["template_id"])
                with self.assertRaisesRegex(ValueError, "not valid for letter"):
                    _business_template(root, visual["template_id"], "letter")

    def test_visual_letter_ignores_legacy_cover_flag(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            visual = self._create(root, "letter")
            with mock.patch(
                "app.business_template_dispatch.render_visual_letter_pdf",
                return_value=b"visual-letter",
            ) as renderer:
                pdf = _render_letter_with_template(
                    root,
                    visual,
                    recipient="Max Muster",
                    subject="Betreff",
                    body="Text",
                    cover=True,
                )
            self.assertEqual(b"visual-letter", pdf)
            renderer.assert_called_once()
            self.assertNotIn("cover", renderer.call_args.kwargs)

    def test_invoice_dispatch_keeps_legacy_path_and_uses_visual_renderer(self):
        row = sample_invoice()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            visual = self._create(root, "invoice")
            with mock.patch(
                "app.business_template_dispatch.render_visual_invoice_pdf", return_value=b"visual-pdf"
            ) as visual_render:
                self.assertEqual(
                    b"visual-pdf", _render_invoice_with_template(root, visual, row)
                )
                visual_render.assert_called_once_with(root, visual["template_id"], row)

            legacy = {"template_id": "legacy", "file": "legacy.pdf"}
            with mock.patch(
                "app.business_template_dispatch._invoice_content_pdf", return_value=b"content"
            ) as content_render, mock.patch(
                "app.business_template_dispatch._merge_content_with_template", return_value=b"legacy-pdf"
            ) as merge:
                self.assertEqual(
                    b"legacy-pdf", _render_invoice_with_template(root, legacy, row)
                )
                content_render.assert_called_once_with(row)
                merge.assert_called_once_with(root, legacy, b"content")

    def test_dynamic_elements_must_be_unique_and_in_content_region(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            template = self._create(root, "invoice")
            row, layout = visual_template(root, template["template_id"])
            layout["elements"].append(dict(next(
                item for item in layout["elements"] if item["type"] == "invoice_totals"
            )))
            layout["elements"][-1]["id"] = "invoice-totals-copy"
            with mock.patch("app.business_template_editor.DocumentStore"):
                with self.assertRaisesRegex(ValueError, "only one invoice_totals"):
                    save_visual_template(root, row["template_id"], row, layout, "tester")


if __name__ == "__main__":
    unittest.main()
