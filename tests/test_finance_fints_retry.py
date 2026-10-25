"""Regression tests for the bounded retry policy on FinTS read requests."""
from __future__ import annotations

import importlib.util
import unittest
from unittest.mock import Mock, patch

from app.finance_fints_retry import is_retryable_transport_error, run_read_with_bounded_retry


class FinTSReadRetryTests(unittest.TestCase):
    def test_retries_at_most_twice_with_exponential_wait(self):
        operation = Mock(side_effect=ConnectionError("temporary disconnect"))
        with patch("app.finance_fints_retry.is_retryable_transport_error", return_value=True), patch(
            "app.finance_fints_retry.random.uniform", side_effect=lambda _low, high: high,
        ), patch("app.finance_fints_retry.time.sleep") as sleep:
            with self.assertRaisesRegex(ConnectionError, "temporary disconnect"):
                run_read_with_bounded_retry(operation)

        self.assertEqual(3, operation.call_count)
        self.assertEqual([0.5, 1.0], [call.args[0] for call in sleep.call_args_list])

    def test_does_not_retry_unclassified_errors(self):
        operation = Mock(side_effect=ValueError("protocol rejection"))
        with patch("app.finance_fints_retry.is_retryable_transport_error", return_value=False), patch(
            "app.finance_fints_retry.time.sleep",
        ) as sleep:
            with self.assertRaisesRegex(ValueError, "protocol rejection"):
                run_read_with_bounded_retry(operation)

        operation.assert_called_once()
        sleep.assert_not_called()

    @unittest.skipUnless(
        importlib.util.find_spec("fints") and importlib.util.find_spec("requests"),
        "requires the optional banking extra",
    )
    def test_retries_only_transient_wrapped_transport_errors(self):
        from fints.exceptions import FinTSConnectionError
        from requests.exceptions import ConnectTimeout, SSLError

        try:
            raise FinTSConnectionError("transport failed") from ConnectTimeout("timed out")
        except FinTSConnectionError as error:
            self.assertTrue(is_retryable_transport_error(error))

        try:
            raise FinTSConnectionError("TLS verification failed") from SSLError("bad certificate")
        except FinTSConnectionError as error:
            self.assertFalse(is_retryable_transport_error(error))

        self.assertFalse(is_retryable_transport_error(
            FinTSConnectionError("Bad status code 503"),
        ))


if __name__ == "__main__":
    unittest.main()
