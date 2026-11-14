"""Regression tests for generated LAN proxy certificates."""
import ipaddress
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography import x509

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
                certificate = x509.load_pem_x509_certificate(first)
                self.assertIn(
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value,
                )
                self.assertEqual(0o600, (Path(directory) / key).stat().st_mode & 0o777)
                with self.assertRaises(ValueError):
                    ensure_self_signed_certificate("127.0.0.2")

    def test_generate_dns_san_for_container_public_host(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.lan_proxy_self_signed.lan_proxy_tls_directory", return_value=Path(directory)):
                cert, _key = ensure_self_signed_certificate("proxy.example.test")
                certificate = x509.load_pem_x509_certificate((Path(directory) / cert).read_bytes())
        self.assertIn(
            x509.DNSName("proxy.example.test"),
            certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value,
        )
        for invalid_name in ("*.example.test", "https://proxy.example.test", "proxy.example.test:8443"):
            with self.subTest(invalid_name=invalid_name), self.assertRaises(ValueError):
                ensure_self_signed_certificate(invalid_name)

    def test_refuse_wildcard(self):
        with self.assertRaises(ValueError):
            ensure_self_signed_certificate("0.0.0.0")


if __name__ == "__main__":
    unittest.main()
