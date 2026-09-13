import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "personnel" / "time_admin.html"


class TimeAuditQuickFilterTests(unittest.TestCase):
    def test_audit_filter_is_present(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn('id="time-audit-filter"', template)
        self.assertIn('data-time-audit-row', template)
        self.assertIn('id="time-audit-count"', template)
        self.assertIn('id="time-audit-empty"', template)
        self.assertIn("input.addEventListener('input',apply)", template)


if __name__ == "__main__":
    unittest.main()
