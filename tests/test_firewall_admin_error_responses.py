from __future__ import annotations

import unittest
from unittest.mock import patch

from flask import g

from app import app, firewall_admin


class FirewallAdminErrorResponseTests(unittest.TestCase):
    def set_admin(self):
        g.user = {
            "id": 1, "username": "admin", "is_admin": True,
            "is_disabled": False,
        }

    def assert_safe_response(self, response, status, secret, expected):
        self.assertEqual(400, status)
        self.assertEqual(expected, response.get_json()["error"])
        self.assertNotIn(secret, response.get_data(as_text=True))

    def test_test_request_does_not_return_internal_validation_details(self):
        secret = "private firewall state path /etc/private/firewall.json"
        with app.test_request_context(
            "/api/mini-services/firewall/test",
            method="POST",
            json={"rules": [{"effect": "allow", "protocol": "tcp", "port": 8080}]},
        ):
            self.set_admin()
            with patch("app.firewall_admin._store") as store, patch(
                "app.firewall_admin._test_rules", side_effect=ValueError(secret),
            ):
                store.return_value.snapshot.return_value = {
                    "conflict": False, "writable": True,
                }
                response, status = firewall_admin.test_change()

        self.assert_safe_response(
            response, status, secret,
            "Firewall-Regel oder Auswahl ist ungültig.",
        )

    def test_confirm_and_rollback_do_not_return_internal_validation_details(self):
        secret = "private firewall state path /etc/private/pending-tests.json"
        for operation, message in (
            (firewall_admin.confirm, "Firewall-Test-ID oder Bestätigung ist ungültig."),
            (firewall_admin.rollback, "Firewall-Test-ID oder Rollback ist ungültig."),
        ):
            with self.subTest(operation=operation.__name__), app.test_request_context(
                f"/api/mini-services/firewall/test/{'a' * 32}/{operation.__name__}",
                method="POST",
            ):
                self.set_admin()
                with patch("app.firewall_admin._store") as store:
                    store.return_value.enqueue.side_effect = ValueError(secret)
                    response, status = operation(ident="a" * 32)
                self.assert_safe_response(response, status, secret, message)


if __name__ == "__main__":
    unittest.main()
