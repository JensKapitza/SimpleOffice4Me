from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.business_template_editor import (
    MAX_ELEMENTS,
    create_visual_template,
    default_visual_layout,
    duplicate_visual_template,
    editor_preview_tokens,
    save_visual_template,
    validate_visual_layout,
    visual_template,
    visual_templates,
)
from app.document_store import CONTROL_DIR


class VisualBusinessTemplateTests(unittest.TestCase):
    def test_default_invoice_layout_is_valid_and_contains_expected_regions(self):
        layout = validate_visual_layout(default_visual_layout("invoice"))
        self.assertEqual("A4", layout["page"]["format"])
        self.assertTrue(layout["regions"]["header"]["enabled"])
        self.assertTrue(layout["regions"]["footer"]["enabled"])
        self.assertFalse(layout["regions"]["margin_left"]["enabled"])
        texts = {row["text"] for row in layout["elements"]}
        self.assertIn("{{firma.name}}", texts)
        self.assertTrue(any("{{rechnung.nummer}}" in text for text in texts))
        self.assertTrue(any("{{firma.iban}}" in text for text in texts))

    def test_visual_store_is_separate_from_legacy_three_page_template_index(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy_dir = root / CONTROL_DIR / "business-templates"
            legacy_dir.mkdir(parents=True)
            legacy_index = legacy_dir / "templates.json"
            legacy = {"templates": [{"template_id": "legacy", "name": "Legacy PDF", "file": "legacy.pdf"}]}
            legacy_index.write_text(json.dumps(legacy), encoding="utf-8")

            with mock.patch("app.business_template_editor.DocumentStore"):
                row = create_visual_template(root, {"name": "Rechnung Standard", "document_type": "invoice"}, "tester")

            self.assertEqual(legacy, json.loads(legacy_index.read_text(encoding="utf-8")))
            self.assertEqual(row["template_id"], visual_templates(root)[0]["template_id"])
            self.assertTrue((legacy_dir / "visual" / f"{row['template_id']}.json").is_file())

    def test_create_save_reload_and_duplicate_visual_template(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch("app.business_template_editor.DocumentStore"):
                row = create_visual_template(root, {"name": "Angebot", "document_type": "quote"}, "tester")
                stored, layout = visual_template(root, row["template_id"])
                layout["free_text"]["freitext1"] = "Referenz 4711"
                layout["elements"][0]["style"]["bold"] = False
                updated = save_visual_template(
                    root,
                    row["template_id"],
                    {"name": "Angebot Blau", "document_type": "quote", "language": "de"},
                    layout,
                    "tester",
                )
                duplicate = duplicate_visual_template(root, row["template_id"], "tester")

            self.assertEqual("Angebot Blau", updated["name"])
            self.assertEqual(2, updated["version"])
            reloaded, saved_layout = visual_template(root, row["template_id"])
            self.assertEqual("Referenz 4711", saved_layout["free_text"]["freitext1"])
            self.assertFalse(saved_layout["elements"][0]["style"]["bold"])
            self.assertEqual("Angebot Blau", reloaded["name"])
            self.assertNotEqual(row["template_id"], duplicate["template_id"])
            self.assertIn("Kopie", duplicate["name"])
            self.assertEqual(2, len(visual_templates(root)))

    def test_layout_validation_rejects_unsafe_or_unbounded_values(self):
        layout = default_visual_layout("invoice")
        layout["elements"][0]["style"]["color"] = "red"
        with self.assertRaisesRegex(ValueError, "#RRGGBB"):
            validate_visual_layout(layout)

        layout = default_visual_layout("invoice")
        layout["elements"][0]["x_mm"] = 999
        with self.assertRaisesRegex(ValueError, "allowed range"):
            validate_visual_layout(layout)

        layout = default_visual_layout("invoice")
        layout["elements"] = [layout["elements"][0]] * (MAX_ELEMENTS + 1)
        with self.assertRaisesRegex(ValueError, "too many"):
            validate_visual_layout(layout)

    def test_layout_validation_rejects_duplicate_ids_and_unknown_types(self):
        layout = default_visual_layout("letter")
        layout["elements"].append(dict(layout["elements"][0]))
        with self.assertRaisesRegex(ValueError, "unique"):
            validate_visual_layout(layout)

        layout = default_visual_layout("letter")
        layout["elements"][0]["type"] = "script"
        with self.assertRaisesRegex(ValueError, "unsupported"):
            validate_visual_layout(layout)

    def test_preview_tokens_use_business_settings_and_free_text(self):
        layout = default_visual_layout("invoice")
        layout["free_text"]["freitext1"] = "Bestellnummer 42"
        tokens = editor_preview_tokens(
            {
                "seller_name": "Kapitza GmbH",
                "seller_street": "Testweg 1",
                "seller_postal": "47137",
                "seller_city": "Duisburg",
                "seller_iban": "DE123",
            },
            layout,
        )
        self.assertEqual("Kapitza GmbH", tokens["firma.name"])
        self.assertIn("Testweg 1", tokens["firma.anschrift"])
        self.assertEqual("DE123", tokens["firma.iban"])
        self.assertEqual("Bestellnummer 42", tokens["freitext1"])


if __name__ == "__main__":
    unittest.main()
