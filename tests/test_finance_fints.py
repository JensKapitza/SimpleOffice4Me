import unittest
from datetime import date
from unittest.mock import patch

from app.finance_fints import default_fetch_window, normalize_transaction
from app.finance_fints_sync import FinTSSyncService
from app.finance_store import FinanceStore
import tempfile
from pathlib import Path


class FinanceFinTSTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = FinanceStore(Path(self.temp.name))
        self.account = self.store.create_account(
            {"name": "ING Giro", "institution": "ING", "iban": "DE02120300000000202051"}, "jens"
        )
        self.connection = self.store.save_bank_connection(
            {"provider": "fints", "institution": "ING", "bank_code": "12030000",
             "endpoint": "https://fints.example.invalid/fints/", "login_id": "user"}, "jens"
        )
        self.store.map_remote_account(
            self.connection["connection_id"], "iban:DE02120300000000202051",
            self.account["account_id"], "DE02120300000000202051", "jens"
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_default_window_overlaps_previous_bookings(self):
        start, end = default_fetch_window("2026-09-15", today=date(2026, 9, 18))
        self.assertEqual(date(2026, 9, 8), start)
        self.assertEqual(date(2026, 9, 18), end)

    def test_normalize_transaction(self):
        row = normalize_transaction({
            "date": date(2026, 9, 18), "entry_date": date(2026, 9, 18),
            "amount": "-18.50", "currency": "EUR", "applicant_name": "Kita",
            "purpose": "September", "end_to_end_reference": "E2E-1"
        })
        self.assertEqual(-1850, row["amount_cents"])
        self.assertEqual("2026-09-18", row["booking_date"])
        self.assertEqual("E2E-1", row["end_to_end_id"])

    @patch("app.finance_fints_sync.fetch_transactions")
    def test_overlap_sync_is_idempotent_without_dropping_real_identical_rows(self, fetch):
        transaction = {
            "booking_date": "2026-09-18", "value_date": "2026-09-18",
            "amount_cents": -18500, "currency": "EUR", "counterparty_name": "Kita",
            "counterparty_iban": "", "purpose": "September", "bank_transaction_id": "",
            "end_to_end_id": "", "mandate_id": "", "raw": {"source": "fints"}
        }
        fetch.return_value = [dict(transaction), dict(transaction)]
        service = FinTSSyncService(self.store)
        first = service.sync_connection(self.connection["connection_id"], "temporary-pin", "jens", today=date(2026, 9, 18))
        self.assertEqual(2, first["possible_duplicates"] + first["created"])
        self.assertEqual(2, len(self.store.bank_transactions(self.account["account_id"], "jens")))

        second = service.sync_connection(self.connection["connection_id"], "temporary-pin", "jens", today=date(2026, 9, 18))
        self.assertEqual(0, second["created"])
        self.assertEqual(2, second["existing"])
        self.assertEqual(2, len(self.store.bank_transactions(self.account["account_id"], "jens")))


if __name__ == "__main__":
    unittest.main()
