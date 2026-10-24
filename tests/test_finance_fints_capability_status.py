"""An unsupported bank read feature must not poison a valid connection status."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.finance_fints import FinTSFeatureUnavailable
from app.finance_fints_sync import FinTSSyncService
from app.finance_store import FinanceStore


class FinTSFeatureStatusTests(unittest.TestCase):
    def test_unsupported_read_operation_keeps_connection_usable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FinanceStore(Path(directory))
            account = store.create_account({"name": "Read-only", "kind": "bank"}, "alice")
            connection = store.save_bank_connection({
                "provider": "fints", "institution": "Testbank", "bank_code": "12345678",
                "endpoint": "https://fints.example.invalid/", "login_id": "test-login",
            }, "alice")
            store.map_remote_account(
                connection["connection_id"], "iban:DE02120300000000202051",
                account["account_id"], "DE02120300000000202051", "alice",
            )
            with patch(
                "app.finance_fints_sync.fetch_transactions",
                side_effect=FinTSFeatureUnavailable("Umsatzabruf nicht unterstützt"),
            ):
                with self.assertRaises(FinTSFeatureUnavailable):
                    FinTSSyncService(store).sync_connection(
                        connection["connection_id"], "request-pin", "alice",
                        today=date(2026, 10, 31),
                    )
            saved = store.bank_connection(connection["connection_id"], "alice")

        self.assertEqual("connected", saved["status"])
        self.assertEqual("", saved.get("last_error", ""))


if __name__ == "__main__":
    unittest.main()
