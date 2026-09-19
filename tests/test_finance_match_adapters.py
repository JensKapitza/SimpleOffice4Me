import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.finance_match_adapters import invoice_candidates, obligation_candidates, proposals_for_transaction
from app.finance_store import FinanceStore


class FinanceMatchAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = FinanceStore(self.root)
        self.account = self.store.create_account({"name": "Giro"}, "jens")

    def tearDown(self):
        self.temp.cleanup()

    @patch("app.finance_match_adapters.invoices")
    @patch("app.finance_match_adapters.invoice_state")
    def test_open_invoice_becomes_candidate(self, state, rows):
        rows.return_value = [{
            "invoice_id": "inv-1", "invoice_number": "RE-2026-0042",
            "due_date": "2026-09-20", "buyer": {"name": "Kunde GmbH"},
            "totals": {"gross": "119.00"}, "status": "final"
        }]
        state.return_value = {"status": "open", "outstanding": "119.00"}
        candidates = invoice_candidates(self.root)
        self.assertEqual(1, len(candidates))
        self.assertEqual(11900, candidates[0].amount_cents)
        self.assertEqual("RE-2026-0042", candidates[0].reference)

    def test_obligation_and_transaction_produce_explainable_proposal(self):
        obligation = self.store.create_obligation({
            "name": "Kita", "kind": "childcare", "direction": "expense",
            "amount_cents": 18500, "recurrence_unit": "monthly",
            "starts_on": "2026-01-01", "counterparty_name": "Kita Muster",
            "reference": "KITA-4711"
        }, "jens")
        tx, _ = self.store.import_bank_transaction({
            "account_id": self.account["account_id"], "booking_date": "2026-09-18",
            "amount_cents": -18500, "counterparty_name": "Kita Muster",
            "purpose": "KITA-4711 September"
        }, "jens")
        with patch("app.finance_match_adapters.invoices", return_value=[]):
            proposals = proposals_for_transaction(self.root, self.store, tx["transaction_id"], "jens")
        self.assertEqual(obligation["obligation_id"], proposals[0]["source_id"])
        self.assertGreaterEqual(proposals[0]["score"], 80)
        self.assertTrue(proposals[0]["reasons"])


if __name__ == "__main__":
    unittest.main()
