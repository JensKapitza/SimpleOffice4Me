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
            elif relative in fragments or template.name.startswith("_"):
                self.assertNotIn("<html", content)
            else:
                self.assertRegex(content, r"\{% extends ['\"]layout\.html['\"] %\}", template.relative_to(ROOT))

    def test_current_frontend_assets_are_loaded_once(self):
        layout = (TEMPLATES / "layout.html").read_text(encoding="utf-8")
        self.assertIn("bootstrap-5.3.8", layout)
        self.assertIn("fontawesome-7.3.1", layout)
        self.assertEqual(1, layout.count("address-autocomplete.js"))
        self.assertNotIn("address_autocomplete.js", layout)

    def test_address_autocomplete_disables_browser_autofill_and_deduplicates(self):
        behavior = (ROOT / "static" / "js" / "address-autocomplete.js").read_text(encoding="utf-8")
        self.assertIn("setAttribute('autocomplete', 'off')", behavior)
        self.assertIn("uniqueBy(payload.suggestions", behavior)
        self.assertIn("suggestion.field === 'postal' && fills.city", behavior)
        for name in ("contact_crm.html", "contact_detail.html", "contact_update_public.html", "business_templates.html"):
            template = (TEMPLATES / "documents" / name).read_text(encoding="utf-8")
            self.assertIn('autocomplete="off"', template, name)

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

    def test_object_billing_sections_are_shared_and_toggle_independently(self):
        listing = (TEMPLATES / "documents" / "objects.html").read_text(encoding="utf-8")
        detail = (TEMPLATES / "documents" / "object_detail.html").read_text(encoding="utf-8")
        behavior = (ROOT / "static" / "js" / "object-billing-sections.js").read_text(encoding="utf-8")

        for template in (listing, detail):
            self.assertIn("data-object-invoice-fields", template)
            self.assertIn("data-object-category-fields", template)
            self.assertEqual(1, template.count("object-billing-sections.js"))
        self.assertIn("invoiceFields.hidden = !invoiceToggle.checked", behavior)
        self.assertIn("categoryFields.hidden = !categoryToggle.checked", behavior)
