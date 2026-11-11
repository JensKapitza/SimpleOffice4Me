from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.business_template_editor import (
    active_visual_template,
    create_visual_template,
    export_visual_template,
    import_visual_template,
    restore_visual_template_version,
    save_visual_template,
    set_active_visual_template,
    visual_template,
    visual_template_versions,
)
from app.business_documents import _business_template, _business_template_choices


class VisualTemplateManagementTests(unittest.TestCase):
    def _create(self, root: Path, name: str, document_type: str = "invoice") -> dict:
        with mock.patch("app.business_template_editor.DocumentStore"):
            return create_visual_template(
                root, {"name": name, "document_type": document_type}, "tester"
            )

    def test_create_and_save_write_version_snapshots(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            row = self._create(root, "Rechnung V1")
            versions = visual_template_versions(root, row["template_id"])
            self.assertEqual([1], [item["version"] for item in versions])

            metadata, layout = visual_template(root, row["template_id"])
            layout["free_text"]["freitext1"] = "Version zwei"
            with mock.patch("app.business_template_editor.DocumentStore"):
                updated = save_visual_template(
                    root, row["template_id"], metadata, layout, "tester"
                )

            self.assertEqual(2, updated["version"])
            self.assertEqual([2, 1], [
                item["version"] for item in visual_template_versions(root, row["template_id"])
            ])

    def test_restore_creates_new_version_instead_of_overwriting_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            row = self._create(root, "Rechnung")
            metadata, layout = visual_template(root, row["template_id"])
            original = layout["free_text"]["freitext1"]
            layout["free_text"]["freitext1"] = "Geändert"
            with mock.patch("app.business_template_editor.DocumentStore"):
                save_visual_template(root, row["template_id"], metadata, layout, "tester")
                restored = restore_visual_template_version(
                    root, row["template_id"], 1, "tester"
                )

            self.assertEqual(3, restored["version"])
            _metadata, restored_layout = visual_template(root, row["template_id"])
            self.assertEqual(original, restored_layout["free_text"]["freitext1"])
            self.assertEqual([3, 2, 1], [
                item["version"] for item in visual_template_versions(root, row["template_id"])
            ])

    def test_activation_is_separate_for_each_document_type(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            invoice = self._create(root, "Rechnung", "invoice")
            letter = self._create(root, "Brief", "letter")
            with mock.patch("app.business_template_editor.DocumentStore"):
                set_active_visual_template(root, invoice["template_id"], "tester")
                set_active_visual_template(root, letter["template_id"], "tester")

            self.assertEqual(
                invoice["template_id"], active_visual_template(root, "invoice")["template_id"]
            )
            self.assertEqual(
                letter["template_id"], active_visual_template(root, "letter")["template_id"]
            )

    def test_export_import_round_trip_uses_new_identity_and_version_one(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._create(root, "Export Rechnung")
            metadata, layout = visual_template(root, source["template_id"])
            layout["free_text"]["freitext2"] = "Importierter Text"
            with mock.patch("app.business_template_editor.DocumentStore"):
                save_visual_template(root, source["template_id"], metadata, layout, "tester")
            payload = export_visual_template(root, source["template_id"])

            with mock.patch("app.business_template_editor.DocumentStore"):
                imported = import_visual_template(root, payload, "importer")

            self.assertNotEqual(source["template_id"], imported["template_id"])
            self.assertEqual(1, imported["version"])
            _row, imported_layout = visual_template(root, imported["template_id"])
            self.assertEqual("Importierter Text", imported_layout["free_text"]["freitext2"])
            self.assertEqual([1], [
                item["version"] for item in visual_template_versions(root, imported["template_id"])
            ])

    def test_import_rejects_unknown_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch("app.business_template_editor.DocumentStore"):
                with self.assertRaisesRegex(ValueError, "unsupported"):
                    import_visual_template(
                        root,
                        {"export_schema": 99, "metadata": {}, "layout": {}},
                        "tester",
                    )

    def test_active_visual_template_wins_default_workflow_choice(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            visual = self._create(root, "Aktive Rechnung")
            with mock.patch("app.business_template_editor.DocumentStore"):
                set_active_visual_template(root, visual["template_id"], "tester")

            legacy = {"template_id": "legacy", "name": "Legacy", "active": True}
            with mock.patch("app.business_documents.templates", return_value=[legacy]):
                choices = _business_template_choices(root, "invoice")

            self.assertFalse(choices[0]["active"])
            self.assertTrue(choices[1]["active"])

            with mock.patch("app.business_documents.active_template") as legacy_default:
                resolved = _business_template(root, "", "invoice")
            legacy_default.assert_not_called()
            self.assertEqual(visual["template_id"], resolved["template_id"])


if __name__ == "__main__":
    unittest.main()
