"""Regression tests for generated LAN proxy certificates."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.lan_proxy_self_signed import ensure_self_signed_certificate


class SelfSignedLanTlsTests(unittest.TestCase):
    def test_generate_and_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.lan_proxy_self_signed.lan_proxy_tls_directory", return_value=Path(directory)):
                cert, key = ensure_self_signed_certificate("127.0.0.1")
                self.assertTrue((Path(directory) / cert).is_file())
                self.assertTrue((Path(directory) / key).is_file())
                first = (Path(directory) / cert).read_bytes()
                self.assertEqual((cert, key), ensure_self_signed_certificate("127.0.0.1"))
                self.assertEqual(first, (Path(directory) / cert).read_bytes())
                with self.assertRaises(ValueError):
                    ensure_self_signed_certificate("127.0.0.2")

    def test_refuse_wildcard(self):
        with self.assertRaises(ValueError):
            ensure_self_signed_certificate("0.0.0.0")


if __name__ == "__main__":
    unittest.main()
