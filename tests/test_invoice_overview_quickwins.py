from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InvoiceOverviewQuickWinsTests(unittest.TestCase):
    def test_quick_filters_and_priority_rows_exist(self):
        template = (ROOT / "templates/documents/invoice_overview.html").read_text(encoding="utf-8")
        self.assertIn("status='open'", template)
        self.assertIn("status='overdue'", template)
        self.assertIn("status='paid'", template)
        self.assertIn("table-danger", template)
        self.assertIn("table-warning", template)
        self.assertIn('id="invoice-search"', template)
        self.assertIn('id="invoice-status-filter"', template)


if __name__ == "__main__":
    unittest.main()
