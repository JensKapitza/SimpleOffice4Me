from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InvoiceCreateStartTests(unittest.TestCase):
    def test_overview_links_to_existing_customer_selection_flow(self):
        overview = (ROOT / "templates/documents/invoice_overview.html").read_text(encoding="utf-8")
        editor = (ROOT / "templates/documents/contact_invoice.html").read_text(encoding="utf-8")
        start = (ROOT / "templates/documents/invoice_create_start.html").read_text(encoding="utf-8")
        route = (ROOT / "app/business_documents.py").read_text(encoding="utf-8")
        contact = (ROOT / "templates/documents/contact_detail.html").read_text(encoding="utf-8")
        detail = (ROOT / "templates/documents/invoice_detail.html").read_text(encoding="utf-8")
        ordering = (ROOT / "static/js/invoice_line_order.js").read_text(encoding="utf-8")

        self.assertIn("url_for('.invoice_create_start')", overview)
        self.assertIn('name="q"', start)
        self.assertIn('name="contact_id"', start)
        self.assertIn('name="task_id"', start)
        self.assertIn('name="draft_updated_at"', editor)
        self.assertIn("_invoice_task(root, task_id, actor, contact_id)", route)
        self.assertIn('"task_id"] = str(form.get("task_id", "")).strip()', route)
        self.assertIn("invoice_create_start', task_id=item.id", contact)
        self.assertIn("linked_task.title", detail)
        self.assertIn("js/invoice_line_order.js", editor)
        self.assertIn("Move position up", editor)
        self.assertIn('name="line_unit"', editor)
        self.assertIn('value="HUR"', editor)
        self.assertIn("body.insertBefore(row, row.previousElementSibling)", ordering)
        self.assertIn("body.insertBefore(row.nextElementSibling, row)", ordering)
        self.assertIn('url_for(".contact_invoice", contact_id=contact_id,', route)
        self.assertIn("contacts.can_manage_contact(contact, actor)", route)


if __name__ == "__main__":
    unittest.main()
