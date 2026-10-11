"""Rate-limit regression tests for public account requests."""
import sqlite3
import unittest

from unittest.mock import patch

from werkzeug.exceptions import BadRequest, TooManyRequests
from app import account_requests

from app import app
from app.account_requests import _check_request_rate


class AccountRequestThrottleTests(unittest.TestCase):
    def test_invalid_submission_still_consumes_throttle_slot(self):
        with app.test_request_context(
            "/auth/account-requests/new", method="POST",
            data={"username": "!", "email": "invalid", "password": "weak"},
        ):
            with patch.object(account_requests, "_registration_enabled", return_value=True), \
                 patch.object(account_requests, "_db") as database, \
                 patch.object(account_requests, "_check_request_rate") as throttle:
                with self.assertRaises(BadRequest):
                    account_requests.new()
                database.assert_called_once_with()
                throttle.assert_called_once_with(database.return_value)

    def test_registration_disabled_during_hash_is_rejected(self):
        from unittest.mock import MagicMock
        from werkzeug.exceptions import Forbidden

        database = MagicMock()
        database.in_transaction = True
        database.execute.return_value.fetchone.return_value = (0,)
        with app.test_request_context(
            "/auth/account-requests/new", method="POST",
            data={"username": "requested-user", "email": "user@example.test",
                  "password": "a-long-secure-password"},
        ):
            with patch.object(account_requests, "_registration_enabled", return_value=True), \
                 patch.object(account_requests, "_db", return_value=database), \
                 patch.object(account_requests, "_check_request_rate"), \
                 patch.object(account_requests, "hash_password", return_value="hashed"):
                with self.assertRaises(Forbidden):
                    account_requests.new()
        self.assertTrue(any(
            call.args[0] == "SELECT enabled FROM registration_settings WHERE id=1"
            for call in database.execute.call_args_list
        ))
        database.rollback.assert_called()

    def test_sixth_request_is_limited_per_client(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute(
            "CREATE TABLE account_request_throttle "
            "(client_key TEXT PRIMARY KEY, window_started INTEGER NOT NULL, attempts INTEGER NOT NULL)"
        )
        try:
            with patch("app.account_requests.time.time", return_value=3600), app.test_request_context(
                "/auth/account-requests/new", method="POST",
                environ_base={"REMOTE_ADDR": "192.0.2.10"},
            ):
                for _ in range(5):
                    _check_request_rate(db)
                with self.assertRaises(TooManyRequests):
                    _check_request_rate(db)
            with patch("app.account_requests.time.time", return_value=3600), app.test_request_context(
                "/auth/account-requests/new", method="POST",
                environ_base={"REMOTE_ADDR": "192.0.2.11"},
            ):
                _check_request_rate(db)
            db.execute("INSERT INTO account_request_throttle(client_key, window_started, attempts) VALUES (?, ?, ?)", ("expired", -10000, 1))
            db.commit()
            with patch("app.account_requests.time.time", return_value=7200):
                with app.test_request_context(
                    "/auth/account-requests/new", method="POST",
                    environ_base={"REMOTE_ADDR": "192.0.2.10"},
                ):
                    _check_request_rate(db)
            self.assertEqual([1, 1], [row["attempts"] for row in db.execute(
                "SELECT attempts FROM account_request_throttle ORDER BY attempts"
            )])
            self.assertEqual(
                2, db.execute("SELECT COUNT(*) FROM account_request_throttle").fetchone()[0]
            )
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
