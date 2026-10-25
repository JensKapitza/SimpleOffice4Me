import base64
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from app import app as flask_app
from app.finance_fints import FinTSAuthenticationRequired, FinTSUnavailable, _client, default_fetch_window, normalize_transaction
from app.finance_fints_sync import FinTSSyncService
from app.finance_store import FinanceStore


class FinanceFinTSTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_secret = flask_app.config.get("SECRET_KEY")
        flask_app.config["SECRET_KEY"] = "finance-fints-sync-sca-test-key"
        self.addCleanup(lambda: flask_app.config.update(SECRET_KEY=self.previous_secret))
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

    def test_fints_client_rejects_unbounded_or_nul_pin_before_connection(self):
        connection = {
            "provider": "fints", "bank_code": "12345678", "login_id": "user",
            "endpoint": "https://bank.example.test/fints",
        }
        for pin in ("x" * 257, "bad\x00pin"):
            with self.subTest(pin_length=len(pin)), self.assertRaisesRegex(ValueError, "PIN-Eingabe"):
                _client(connection, pin)

    def test_fints_client_rejects_disabled_bank_connection(self):
        connection = {
            "provider": "fints", "status": "disabled", "bank_code": "12345678",
            "login_id": "user", "endpoint": "https://bank.example.test/fints",
        }
        with self.assertRaisesRegex(ValueError, "deaktiviert"):
            _client(connection, "temporary-pin")

    def test_fints_requires_registered_product_id_before_client_creation(self):
        connection = {
            "provider": "fints", "bank_code": "12345678", "login_id": "user",
            "endpoint": "https://bank.example.test/fints",
        }
        client_type = Mock()
        with patch.dict(os.environ, {"SIMPLEOFFICE_FINTS_PRODUCT_ID": ""}), patch(
            "app.finance_fints._library", return_value=client_type,
        ) as load_library:
            with self.assertRaisesRegex(FinTSUnavailable, "registrierte Produkt-ID"):
                _client(connection, "temporary-pin")
        load_library.assert_called_once_with()
        client_type.assert_not_called()

    def test_fints_rejects_unprintable_or_overlong_product_id(self):
        connection = {
            "provider": "fints", "bank_code": "12345678", "login_id": "user",
            "endpoint": "https://bank.example.test/fints",
        }
        invalid_values = ("invalid\nproduct", "x" * 65)
        for product_id in invalid_values:
            with self.subTest(product_id=product_id), patch.dict(
                os.environ, {"SIMPLEOFFICE_FINTS_PRODUCT_ID": product_id},
            ), patch("app.finance_fints._library", return_value=Mock()) as load_library:
                with self.assertRaisesRegex(FinTSUnavailable, "Produkt-ID fehlt oder ist ungültig"):
                    _client(connection, "temporary-pin")
                load_library.return_value.assert_not_called()

    def test_fints_passes_registered_product_id_to_client(self):
        connection = {
            "provider": "fints", "bank_code": "12345678", "login_id": "user",
            "endpoint": "https://bank.example.test/fints",
        }
        client_type = Mock()
        with patch.dict(os.environ, {"SIMPLEOFFICE_FINTS_PRODUCT_ID": "registered-test-product"}), patch(
            "app.finance_fints._library", return_value=client_type,
        ):
            client = _client(connection, "temporary-pin")

        self.assertIs(client_type.return_value, client)
        client_type.assert_called_once_with(
            "12345678", "user", "temporary-pin", "https://bank.example.test/fints",
            product_id="registered-test-product",
        )

    @patch("app.finance_fints_sync.fetch_transactions")
    def test_disabled_connection_cannot_be_reenabled_by_sync(self, fetch):
        connection_id = self.connection["connection_id"]
        self.store.update_bank_connection_status(connection_id, "jens", "disabled")
        with self.assertRaisesRegex(ValueError, "deaktiviert"):
            FinTSSyncService(self.store).sync_connection(connection_id, "temporary-pin", "jens")
        fetch.assert_not_called()
        self.assertEqual("disabled", self.store.bank_connection(connection_id, "jens")["status"])

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
        self.assertTrue(all(tx["state"] == "already_imported" for tx in second["accounts"][0]["transactions"]))
        self.assertEqual(2, len(self.store.bank_transactions(self.account["account_id"], "jens")))

    @patch("app.finance_fints_retry.time.sleep")
    @patch("app.finance_fints_retry.is_retryable_transport_error", return_value=True)
    @patch("app.finance_fints_sync.fetch_transactions")
    def test_fresh_read_retries_transient_transport_error_before_import(
        self, fetch, is_retryable, sleep,
    ):
        fetch.side_effect = [ConnectionResetError("temporary disconnect"), []]

        result = FinTSSyncService(self.store).sync_connection(
            self.connection["connection_id"], "temporary-pin", "jens",
            today=date(2026, 9, 18),
        )

        self.assertEqual(2, fetch.call_count)
        self.assertEqual(1, is_retryable.call_count)
        sleep.assert_called_once()
        self.assertEqual(0, result["fetched"])
        self.assertEqual("connected", self.store.bank_connection(
            self.connection["connection_id"], "jens",
        )["status"])

    @patch("app.finance_fints_retry.is_retryable_transport_error", return_value=True)
    @patch("app.finance_fints_sync.fetch_transactions", side_effect=TimeoutError("lost response"))
    def test_sca_continuation_is_never_retried(self, fetch, is_retryable):
        challenge_state = {
            "operation": "transactions",
            "remote_account_id": "iban:DE02120300000000202051",
            "local_account_id": self.account["account_id"],
            "start_date": "2026-09-01",
            "end_date": "2026-09-18",
        }

        with self.assertRaisesRegex(TimeoutError, "lost response"):
            FinTSSyncService(self.store).sync_mapping(
                self.connection["connection_id"],
                {
                    "account_id": self.account["account_id"],
                    "remote_account_id": challenge_state["remote_account_id"],
                },
                "temporary-pin", "jens", challenge_state=challenge_state, tan="123456",
            )

        fetch.assert_called_once()
        is_retryable.assert_not_called()

    @patch("app.finance_fints_sync.fetch_transactions")
    def test_transaction_sca_resumes_without_replaying_completed_mappings(self, fetch):
        second_account = self.store.create_account({"name": "Zweiter Giro", "kind": "bank"}, "jens")
        second_remote_id = "iban:DE89370400440532013000"
        self.store.map_remote_account(
            self.connection["connection_id"], second_remote_id,
            second_account["account_id"], "DE89370400440532013000", "jens",
        )
        start, end = default_fetch_window(None, today=date(2026, 10, 31))
        first_mapping = {
            "booking_date": start.isoformat(), "value_date": start.isoformat(),
            "amount_cents": -100, "currency": "EUR", "counterparty_name": "First",
            "counterparty_iban": "", "purpose": "first transaction", "bank_transaction_id": "bank-1",
            "end_to_end_id": "", "mandate_id": "", "raw": {"source": "fints"},
        }
        second_mapping = {
            **first_mapping, "counterparty_name": "Second", "purpose": "second transaction",
            "bank_transaction_id": "bank-2",
        }
        state = {
            "version": 1, "operation": "transactions", "step": "transactions",
            "client_data": base64.b64encode(b"client").decode("ascii"),
            "dialog_data": base64.b64encode(b"dialog").decode("ascii"),
            "challenge_data": base64.b64encode(b"challenge").decode("ascii"),
            "remote_account_id": second_remote_id, "account_currency": "EUR",
            "challenge_text": "Approve transaction read", "decoupled": False,
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "local_account_id": "", "completed_mappings": [],
        }
        fetch.side_effect = [
            [first_mapping],
            FinTSAuthenticationRequired("TAN required", challenge_state=state),
        ]
        service = FinTSSyncService(self.store)
        with self.assertRaises(FinTSAuthenticationRequired) as challenge:
            service.sync_connection(
                self.connection["connection_id"], "temporary-pin", "jens",
                today=date(2026, 10, 31),
            )
        challenge_state = challenge.exception.challenge_state
        self.assertEqual(self.account["account_id"], challenge_state["completed_mappings"][0]["account_id"])
        self.assertEqual(second_account["account_id"], challenge_state["local_account_id"])
        self.assertEqual(1, len(self.store.bank_transactions(self.account["account_id"], "jens")))
        self.assertEqual([], self.store.bank_transactions(second_account["account_id"], "jens"))

        with flask_app.app_context():
            token = self.store.create_fints_challenge(
                self.connection["connection_id"], challenge_state, "jens",
            )
            resumed_state = self.store.consume_fints_challenge(
                token, self.connection["connection_id"], "jens",
            )
        fetch.side_effect = [[second_mapping]]
        fetch.reset_mock()
        result = service.sync_connection(
            self.connection["connection_id"], "temporary-pin", "jens",
            challenge_state=resumed_state, tan="123456",
        )

        self.assertEqual(["ING Giro"], result["previously_synced_accounts"])
        self.assertEqual(1, result["created"])
        self.assertEqual(1, len(result["accounts"]))
        self.assertEqual(second_account["account_id"], result["accounts"][0]["account_id"])
        self.assertEqual(1, fetch.call_count)
        self.assertEqual(1, len(self.store.bank_transactions(self.account["account_id"], "jens")))
        self.assertEqual(1, len(self.store.bank_transactions(second_account["account_id"], "jens")))


if __name__ == "__main__":
    unittest.main()
