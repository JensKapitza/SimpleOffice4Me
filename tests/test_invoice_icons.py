"""Invoice icon contracts use the existing accessible icon system."""
from pathlib import Path
import unittest


class InvoiceIconTests(unittest.TestCase):
    def test_invoice_actions_keep_visible_labels_and_decorative_icons(self):
        billing = Path("templates/documents/customer_billing.html").read_text(encoding="utf-8")
        overview = Path("templates/documents/invoice_overview.html").read_text(encoding="utf-8")
        detail = Path("templates/documents/invoice_detail.html").read_text(encoding="utf-8")
        draft = Path("templates/documents/contact_invoice.html").read_text(encoding="utf-8")

        self.assertIn("'Invoice actions' if g.language == 'en' else 'Rechnungsaktionen'", billing)
        self.assertIn("'Invoice actions' if g.language == 'en' else 'Rechnungsaktionen'", detail)
        self.assertIn('fa-file-circle-plus me-1" aria-hidden="true"></i>{{ tr(\'billing.new_invoice\') }}', billing)
        self.assertIn('fa-files me-1" aria-hidden="true"></i>{{ tr(\'billing.download_all\') }}', billing)
        self.assertIn('fa-folder-open me-1" aria-hidden="true"></i>{{ tr(\'billing.customer_archive\') }}', billing)
        self.assertIn('fa-file-invoice me-1" aria-hidden="true"></i>{{ tr(\'invoice.open\') }}', billing)
        self.assertIn('fa-file-pdf me-1" aria-hidden="true"></i>{{ tr(\'billing.download\') }}', billing)
        self.assertIn('fa-file-invoice me-1" aria-hidden="true"></i>{{ tr(\'invoice.open\') }}', overview)
        self.assertIn('fa-file-pdf me-1" aria-hidden="true"></i>{{ tr(\'billing.download\') }}', overview)
        self.assertIn('fa-file-invoice me-2" aria-hidden="true"></i>{{ tr(\'invoice.title\') }}', detail)
        self.assertIn('fa-chart-column me-1" aria-hidden="true"></i>EÜR', detail)
        self.assertIn("fa-file-pen' if draft else 'fa-file-circle-plus", draft)

    def test_related_invoice_actions_use_the_shared_fontawesome_assets(self):
        layout = Path("templates/layout.html").read_text(encoding="utf-8")
        finance = Path("templates/documents/v3_finance_detail.html").read_text(encoding="utf-8")
        icons = Path("static/vendor/fontawesome-7.3.1/css/all.min.css").read_text(encoding="utf-8")

        self.assertIn("vendor/fontawesome-7.3.1/css/all.min.css", layout)
        self.assertIn('fa-files me-1" aria-hidden="true"></i>Nachfolger', finance)
        self.assertIn('fa-link me-1" aria-hidden="true"></i>{{ item.predecessor_id }}', finance)
        self.assertIn('fa-code-branch me-1" aria-hidden="true"></i>', finance)
        for name in ("file-circle-plus", "file-pen", "files", "link", "file-invoice", "file-pdf", "code-branch", "folder-open", "chart-column"):
            self.assertIn(f".fa-{name}", icons)


if __name__ == "__main__":
    unittest.main()
