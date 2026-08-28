import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"


class TemplateShellTests(unittest.TestCase):
    def test_all_templates_use_the_translatable_layout(self):
        fragments = {"documents/event_list.html", "documents/invoice_positions.html", "documents/nav.html"}
        for template in TEMPLATES.rglob("*.html"):
            content = template.read_text(encoding="utf-8")
            relative = str(template.relative_to(TEMPLATES))
            if template.name == "layout.html":
                self.assertIn("i18n.js", content)
                self.assertIn('lang="{{ g.language', content)
            elif relative in fragments:
                self.assertNotIn("<html", content)
            else:
                self.assertRegex(content, r"\{% extends ['\"]layout\.html['\"] %\}", template.relative_to(ROOT))

    def test_current_frontend_assets_are_loaded_once(self):
        layout = (TEMPLATES / "layout.html").read_text(encoding="utf-8")
        self.assertIn("bootstrap-5.3.8", layout)
        self.assertIn("fontawesome-7.3.1", layout)
        self.assertEqual(1, layout.count("address-autocomplete.js"))
        self.assertNotIn("address_autocomplete.js", layout)

    def test_document_index_uses_collapsible_folder_tree(self):
        index = (TEMPLATES / "documents" / "index.html").read_text(encoding="utf-8")
        self.assertIn("data-document-tree", index)
        self.assertIn("Alle aufklappen", index)

    def test_main_navigation_stacks_and_scrolls_responsively(self):
        navigation = (TEMPLATES / "documents" / "nav.html").read_text(encoding="utf-8")
        stylesheet = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
        self.assertIn("navbar-expand-xxl", navigation)
        self.assertIn("navigation-primary", navigation)
        self.assertIn("navigation-actions", navigation)
        self.assertIn("@media (max-width: 1399.98px)", stylesheet)
        self.assertIn("overflow-y: auto", stylesheet)
        self.assertIn("@media (min-width: 1400px)", stylesheet)
        self.assertIn(".app-navbar .navigation-primary", stylesheet)
        self.assertIn("flex-wrap: wrap", stylesheet)
