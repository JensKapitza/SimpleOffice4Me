"""Regression tests for login QR display and private-network visibility."""
import unittest
from unittest.mock import patch
from app import app
from app.login_qr import login_qr_targets


class LoginQRCodeTests(unittest.TestCase):
    def test_local_client_sees_interface_candidates(self):
        with app.test_request_context("/auth/login", base_url="http://127.0.0.1:8080",
                                      environ_base={"REMOTE_ADDR": "127.0.0.1"}):
            with patch("app.login_qr._addresses", return_value=["192.168.1.22"]):
                targets = login_qr_targets()
        self.assertEqual("http://127.0.0.1:8080/auth/login", targets[0]["url"])
        self.assertIn("http://192.168.1.22:8080/auth/login", [item["url"] for item in targets])
        self.assertEqual(len(targets), len({item["url"] for item in targets}))

    def test_remote_client_cannot_enumerate_private_interfaces(self):
        with app.test_request_context("/auth/login", base_url="https://office.example",
                                      environ_base={"REMOTE_ADDR": "198.51.100.27"}):
            with patch("app.login_qr._addresses", return_value=["192.168.1.22"]):
                targets = login_qr_targets()
        self.assertEqual(["https://office.example/auth/login"], [item["url"] for item in targets])

    def test_login_page_contains_swipe_carousel(self):
        with app.test_client() as client:
            response = client.get("/auth/login")
        self.assertEqual(200, response.status_code)
        self.assertIn(b'id="login-qr-carousel"', response.data)
        self.assertIn(b'data-qr-slide', response.data)


if __name__ == "__main__":
    unittest.main()
