"""Rate-limit regression tests for public account requests."""
import sqlite3
import unittest

from unittest.mock import patch

from werkzeug.exceptions import TooManyRequests

from app import app
from app.account_requests import _check_request_rate


class AccountRequestThrottleTests(unittest.TestCase):
    def test_sixth_request_is_limited_per_client(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute(
            "CREATE TABLE account_request_throttle "
            "(client_key TEXT PRIMARY KEY, window_started INTEGER NOT NULL, attempts INTEGER NOT NULL)"
        )
        try:
            with app.test_request_context(
                "/auth/account-requests/new", method="POST",
                environ_base={"REMOTE_ADDR": "192.0.2.10"},
            ):
                for _ in range(5):
                    _check_request_rate(db)
                with self.assertRaises(TooManyRequests):
                    _check_request_rate(db)
            with app.test_request_context(
                "/auth/account-requests/new", method="POST",
                environ_base={"REMOTE_ADDR": "192.0.2.11"},
            ):
                _check_request_rate(db)
            with patch("app.account_requests.time.time", return_value=7200):
                with app.test_request_context(
                    "/auth/account-requests/new", method="POST",
                    environ_base={"REMOTE_ADDR": "192.0.2.10"},
                ):
                    _check_request_rate(db)
            attempts = db.execute(
                "SELECT attempts FROM account_request_throttle WHERE client_key IN "
                "(SELECT client_key FROM account_request_throttle ORDER BY attempts DESC LIMIT 1)"
            ).fetchone()["attempts"]
            self.assertEqual(1, attempts)
            self.assertEqual(
                2, db.execute("SELECT COUNT(*) FROM account_request_throttle").fetchone()[0]
            )
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
