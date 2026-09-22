import tempfile
import unittest
from pathlib import Path

from tools.check_secret_leaks import scan_repository, scan_text


class SecretLeakGateTests(unittest.TestCase):
    def test_detects_provider_token_without_echoing_value(self):
        token = "ghp_" + ("A1b2" * 9)
        findings = scan_text("value = " + token, "fixture.txt")
        self.assertEqual("github-token", findings[0].kind)
        self.assertEqual("fixture.txt", findings[0].path)

    def test_detects_private_key_header(self):
        marker = "-----BEGIN " + "PRIVATE KEY-----"
        findings = scan_text(marker, "key.pem")
        self.assertEqual(["private-key"], [item.kind for item in findings])

    def test_detects_high_entropy_literal_assignment(self):
        value = "aB3$kL9!qW2@zX7#nM5%rT8&"
        findings = scan_text('client_secret = "' + value + '"', "config.py")
        self.assertEqual(["literal-secret"], [item.kind for item in findings])

    def test_allows_explicit_placeholder_literal(self):
        text = 'token = "ci-placeholder-not-a-real-token"'
        self.assertEqual([], scan_text(text, "workflow.yml"))

    def test_repository_scan_skips_binary_and_ignored_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "safe.txt").write_text("nothing sensitive", encoding="utf-8")
            (root / "binary.bin").write_bytes(b"\x00" + b"ghp_" + (b"A" * 36))
            ignored = root / ".git"
            ignored.mkdir()
            (ignored / "secret.txt").write_text("ghp_" + ("A" * 36), encoding="utf-8")
            self.assertEqual([], scan_repository(root))


if __name__ == "__main__":
    unittest.main()
