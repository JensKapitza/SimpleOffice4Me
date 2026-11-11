from __future__ import annotations

import unittest
from pathlib import Path


class BusinessTemplateEditorAssetTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.script = (self.root / "static/js/business-template-editor.js").read_text(encoding="utf-8")
        self.template = (self.root / "templates/documents/business_template_editor.html").read_text(encoding="utf-8")
        self.styles = (self.root / "static/css/business-template-editor.css").read_text(encoding="utf-8")

    def test_editor_exposes_history_snap_layer_and_keyboard_controls(self):
        for marker in (
            'data-action="undo"',
            'data-action="redo"',
            "data-snap-grid",
            'data-action="layer-up"',
            'data-action="layer-down"',
            "data-element-visible",
            'data-prop="font_family"',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.template)

        for marker in (
            "function commitHistory()",
            "function restoreHistory(index)",
            "function undo()",
            "function redo()",
            "ArrowLeft",
            "ArrowRight",
            "ArrowUp",
            "ArrowDown",
            "event.key === 'Delete'",
            "snapGrid",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.script)

    def test_preview_builds_safe_dom_without_inner_html(self):
        self.assertNotIn("innerHTML", self.script)
        self.assertIn("document.createTextNode", self.script)
        self.assertIn("span.textContent = run.text", self.script)

    def test_canvas_has_visible_grid_and_keyboard_focus_styles(self):
        self.assertIn(".template-canvas.snap-grid", self.styles)
        self.assertIn(".template-element:focus-visible", self.styles)


if __name__ == "__main__":
    unittest.main()
