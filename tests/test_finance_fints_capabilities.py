"""Read-only FinTS capabilities are normalized and enforced without exposing bank state."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.finance_fints import (
    FinTSAuthenticationRequired,
    FinTSFeatureUnavailable,
    _require_read_capability,
    discover_accounts,
    discover_accounts_with_capabilities,
    fetch_transactions,
)
from app.finance_fints_balances import fetch_balance


class Operation(Enum):
    GET_BALANCE = 1
    GET_TRANSACTIONS = 2
    GET_TRANSACTIONS_XML = 3
    SEPA_TRANSFER_SINGLE = 4


class FakeClient:
    def __init__(self, information=None):
        self.information = information or {}
        self.accounts = [SimpleNamespace(
            iban="DE02120300000000202051", bic="TESTDEFF", accountnumber="202051",
            subaccount="", bank_identifier="12345678", currency="EUR",
            account_type="Girokonto",
        )]
        self.get_balance = Mock(return_value=SimpleNamespace(amount=Decimal("10.00"), currency="EUR"))
        self.get_transactions = Mock(return_value=[])

    def get_sepa_accounts(self):
        return self.accounts

    def get_information(self):
        return self.information


class FinTSCapabilityTests(unittest.TestCase):
    remote_id = "iban:DE02120300000000202051"
    connection = {
        "provider": "fints", "status": "configured", "bank_code": "12345678",
        "login_id": "test-login", "endpoint": "https://fints.example.invalid/",
    }
    account = SimpleNamespace(
        iban="DE02120300000000202051", bic="TESTDEFF", accountnumber="202051",
        subaccount="", bank_identifier="12345678", currency="EUR",
    )

    @staticmethod
    def information(*, balance=True, transactions=True, account_balance=None):
        account_operations = {
            Operation.GET_BALANCE: account_balance,
            Operation.GET_TRANSACTIONS: transactions,
            Operation.GET_TRANSACTIONS_XML: transactions,
        }
        account_operations = {key: value for key, value in account_operations.items() if value is not None}
        mechanism = SimpleNamespace(
            name="pushTAN", VERSION=7, tan_process="2", challenge_structured=True,
        )
        return {
            "bank": {"supported_operations": {
                Operation.GET_BALANCE: balance,
                Operation.GET_TRANSACTIONS: transactions,
                Operation.GET_TRANSACTIONS_XML: transactions,
                Operation.SEPA_TRANSFER_SINGLE: True,
            }},
            "accounts": [{
                "iban": "DE02120300000000202051",
                "bank_identifier": SimpleNamespace(bank_code="12345678"),
                "account_number": "202051", "subaccount_number": "",
                "owner_name": ["Private owner that must not be returned"],
                "supported_operations": account_operations,
            }],
            "auth": {
                "current_tan_mechanism": "942",
                "tan_mechanisms": {"942": mechanism},
            },
        }

    def test_discovery_returns_bounded_read_capabilities_and_tan_methods(self):
        client = FakeClient(self.information(account_balance=False))
        with patch("app.finance_fints._client", return_value=client):
            accounts, profile = discover_accounts_with_capabilities(self.connection, "request-pin")

        self.assertEqual(self.remote_id, accounts[0]["remote_account_id"])
        self.assertTrue(profile["read"]["balance"])
        self.assertTrue(profile["read"]["transactions"])
        self.assertFalse(profile["accounts"][self.remote_id]["balance"])
        self.assertTrue(profile["accounts"][self.remote_id]["transactions"])
        self.assertEqual("942", profile["current_tan_mechanism"])
        self.assertEqual("pushTAN", profile["current_tan_mechanism_label"])
        self.assertEqual([{
            "security_function": "942", "name": "pushTAN", "version": 7,
            "tan_process": "2", "challenge_structured": True,
        }], profile["tan_mechanisms"])
        self.assertNotIn("SEPA_TRANSFER_SINGLE", repr(profile))
        self.assertNotIn("Private owner", repr(profile))
        self.assertNotIn("request-pin", repr(profile))

    def test_legacy_account_discovery_does_not_require_capability_api(self):
        client = SimpleNamespace(get_sepa_accounts=lambda: [self.account])
        with patch("app.finance_fints._client", return_value=client):
            accounts = discover_accounts(self.connection, "request-pin")
        self.assertEqual(self.remote_id, accounts[0]["remote_account_id"])

    def test_fresh_account_discovery_retries_transient_transport_failure(self):
        failed = FakeClient()
        failed.get_sepa_accounts = Mock(side_effect=ConnectionResetError("temporary disconnect"))
        success = FakeClient()
        with patch("app.finance_fints._client", side_effect=[failed, success]) as make_client, patch(
            "app.finance_fints_retry.is_retryable_transport_error", return_value=True,
        ) as is_retryable, patch("app.finance_fints_retry.time.sleep") as sleep:
            accounts = discover_accounts(self.connection, "request-pin")

        self.assertEqual(2, make_client.call_count)
        self.assertEqual(1, is_retryable.call_count)
        failed.get_sepa_accounts.assert_called_once()
        sleep.assert_called_once()
        self.assertEqual(self.remote_id, accounts[0]["remote_account_id"])

    def test_fresh_capability_discovery_retries_transient_transport_failure(self):
        failed = FakeClient(self.information())
        failed.get_information = Mock(side_effect=ConnectionResetError("temporary disconnect"))
        success = FakeClient(self.information())
        with patch("app.finance_fints._client", side_effect=[failed, success]) as make_client, patch(
            "app.finance_fints_retry.is_retryable_transport_error", return_value=True,
        ) as is_retryable, patch("app.finance_fints_retry.time.sleep") as sleep:
            accounts, profile = discover_accounts_with_capabilities(
                self.connection, "request-pin",
            )

        self.assertEqual(2, make_client.call_count)
        self.assertEqual(1, is_retryable.call_count)
        failed.get_information.assert_called_once()
        sleep.assert_called_once()
        self.assertEqual(self.remote_id, accounts[0]["remote_account_id"])
        self.assertTrue(profile["read"]["balance"])

    def test_account_discovery_tan_challenge_is_not_retried(self):
        class NeedTANResponse:
            challenge = "approve account lookup"

            def get_data(self):
                return b"retry-state"

        client = FakeClient()
        client.get_sepa_accounts = Mock(return_value=NeedTANResponse())
        with patch("app.finance_fints._client", return_value=client) as make_client, patch(
            "app.finance_fints_retry.time.sleep",
        ) as sleep:
            with self.assertRaises(FinTSAuthenticationRequired):
                discover_accounts(self.connection, "request-pin")

        make_client.assert_called_once()
        client.get_sepa_accounts.assert_called_once()
        sleep.assert_not_called()

    def test_absent_or_incomplete_bank_information_stays_unknown(self):
        client = FakeClient({"bank": {}, "auth": {}})
        with patch("app.finance_fints._client", return_value=client):
            _accounts, profile = discover_accounts_with_capabilities(self.connection, "request-pin")
        self.assertIsNone(profile["read"]["balance"])
        self.assertIsNone(profile["read"]["transactions"])
        self.assertEqual([], profile["tan_mechanisms"])

    def test_one_step_pin_is_reported_without_inventing_a_tan_method(self):
        client = FakeClient({"auth": {"current_tan_mechanism": "999", "tan_mechanisms": {}}})
        with patch("app.finance_fints._client", return_value=client):
            _accounts, profile = discover_accounts_with_capabilities(self.connection, "request-pin")
        self.assertEqual("Ein-Schritt-Verfahren (PIN)", profile["current_tan_mechanism_label"])
        self.assertEqual([], profile["tan_mechanisms"])

    def test_balance_and_transaction_calls_fail_closed_on_explicitly_unsupported_features(self):
        client = FakeClient(self.information(balance=False, transactions=False))
        with patch("app.finance_fints_balances._client", return_value=client), patch(
            "app.finance_fints_balances._find_remote_account", return_value=self.account,
        ):
            with self.assertRaises(FinTSFeatureUnavailable):
                fetch_balance(self.connection, self.remote_id, "request-pin")
        client.get_balance.assert_not_called()

        account_restricted = FakeClient(self.information(account_balance=False))
        with patch("app.finance_fints_balances._client", return_value=account_restricted), patch(
            "app.finance_fints_balances._find_remote_account", return_value=self.account,
        ):
            with self.assertRaises(FinTSFeatureUnavailable):
                fetch_balance(self.connection, self.remote_id, "request-pin")
        account_restricted.get_balance.assert_not_called()

        client = FakeClient(self.information(balance=True, transactions=False))
        with patch("app.finance_fints._client", return_value=client), patch(
            "app.finance_fints._find_remote_account", return_value=self.account,
        ):
            with self.assertRaises(FinTSFeatureUnavailable):
                fetch_transactions(
                    self.connection, self.remote_id, "request-pin",
                    start_date=date(2026, 10, 1), end_date=date(2026, 10, 31),
                )
        client.get_transactions.assert_not_called()

    def test_read_guard_allows_unknown_capability_to_keep_legacy_bank_compatibility(self):
        client = FakeClient({"bank": {"supported_operations": {Operation.GET_BALANCE: True}}})
        _require_read_capability(client, self.remote_id, "balance")
        _require_read_capability(client, self.remote_id, "transactions")

if __name__ == "__main__":
    unittest.main()
