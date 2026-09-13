from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AdminLogQuickWinsTests(unittest.TestCase):
    def test_audit_filters_have_labels_and_fast_outcome_links(self):
        template = (ROOT / "templates/admin/logs.html").read_text(encoding="utf-8")
        for marker in ("audit-q", "audit-actor", "audit-action", "audit-target-type", "audit-request-id", "audit-outcome"):
            self.assertIn(marker, template)
        self.assertIn("Nur fehlgeschlagen", template)
        self.assertIn("Nur abgewiesen", template)
        self.assertIn("table-danger", template)
        self.assertIn("table-warning", template)


if __name__ == "__main__":
    unittest.main()
