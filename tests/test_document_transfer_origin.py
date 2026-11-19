import unittest
from unittest.mock import patch

from app import app
from app.document_transfer_origin import download_address


class DownloadOriginTests(unittest.TestCase):
    @patch("app.document_transfer_origin._lan_proxy_status")
    def test_loopback_uses_running_lan_proxy(self, status):
        with app.test_request_context(base_url="http://[::1]:8080"):
            status.return_value = {"state": "running", "origin": "https://[fd00::1]:9443"}
            url, local = download_address("abc")
            self.assertEqual("https://[fd00::1]:9443/documents/abc/transfer/download", url)
            self.assertFalse(local)
            status.return_value["state"] = "stopped"
            self.assertEqual(("http://[::1]:8080/documents/abc/transfer/download", True), download_address("abc"))

    @patch("app.document_transfer_origin._lan_proxy_status")
    def test_existing_reachable_origin_is_preserved(self, status):
        with app.test_request_context(base_url="https://office.example"):
            self.assertEqual(("https://office.example/documents/abc/transfer/download", False), download_address("abc"))
        status.assert_not_called()
