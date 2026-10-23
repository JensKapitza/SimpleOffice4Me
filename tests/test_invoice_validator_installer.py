import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "install_invoice_validator.py"
SPEC = importlib.util.spec_from_file_location("install_invoice_validator", SCRIPT)
installer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(installer)


class InvoiceValidatorInstallerTests(unittest.TestCase):
    def test_upstream_release_checksum_is_pinned_and_payload_is_verified(self):
        payload = b"executable validator jar"

        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / installer.FILENAME
            checksum_file = target.with_suffix(".jar.sha256")
            with mock.patch.object(installer, "TARGET", target), \
                 mock.patch.object(installer, "CHECKSUM_FILE", checksum_file), \
                 mock.patch.object(installer, "_download_upstream_release", return_value=payload), \
                 mock.patch.object(installer.shutil, "which", return_value="/usr/bin/java"):
                with self.assertRaisesRegex(RuntimeError, "checksum does not match"):
                    installer.install()

            self.assertFalse(target.exists())
            self.assertFalse(checksum_file.exists())

    def test_valid_sha256_installs_jar(self):
        payload = b"executable validator jar"
        checksum = hashlib.sha256(payload).hexdigest().encode("ascii")

        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / installer.FILENAME
            checksum_file = target.with_suffix(".jar.sha256")
            with mock.patch.object(installer, "TARGET", target), \
                 mock.patch.object(installer, "CHECKSUM_FILE", checksum_file), \
                 mock.patch.object(installer, "_published_checksum", return_value=checksum.decode()), \
                 mock.patch.object(installer, "_download_upstream_release", return_value=payload), \
                 mock.patch.object(installer.shutil, "which", return_value="/usr/bin/java"):
                installed = installer.install()

            self.assertEqual(target, installed)
            self.assertEqual(payload, target.read_bytes())
            self.assertEqual(hashlib.sha256(payload).hexdigest(), checksum_file.read_text().strip())

    def test_checksum_mismatch_does_not_install_jar(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / installer.FILENAME
            with mock.patch.object(installer, "TARGET", target), \
                 mock.patch.object(installer, "CHECKSUM_FILE", target.with_suffix(".jar.sha256")), \
                 mock.patch.object(installer, "_download_upstream_release", return_value=b"payload"), \
                 mock.patch.object(installer.shutil, "which", return_value="/usr/bin/java"):
                with self.assertRaisesRegex(RuntimeError, "checksum does not match"):
                    installer.install()
            self.assertFalse(target.exists())

    def test_download_rejects_non_maven_or_credentialed_urls_before_network(self):
        bad_urls = (
            "http://repo1.maven.org/maven2/org/mustangproject/Mustang-CLI/2.25.0/x.jar",
            "https://repo1.maven.org.evil.invalid/maven2/org/mustangproject/Mustang-CLI/2.25.0/x.jar",
            "https://user:secret@repo1.maven.org/maven2/org/mustangproject/Mustang-CLI/2.25.0/x.jar",
            "https://repo1.maven.org:444/maven2/org/mustangproject/Mustang-CLI/2.25.0/x.jar",
            "https://repo1.maven.org/maven2/other/package.jar",
        )
        with mock.patch.object(installer._OPENER, "open") as opener:
            for url in bad_urls:
                with self.subTest(url=url), self.assertRaises(RuntimeError):
                    installer._download(url, max_bytes=1024)
            opener.assert_not_called()

    def test_github_release_redirect_rejects_untrusted_hosts(self):
        handler = installer._GitHubAssetRedirectHandler()
        for url in ("http://release-assets.githubusercontent.com/file", "https://evil.invalid/file"):
            with self.subTest(url=url), self.assertRaises(RuntimeError):
                handler.redirect_request(mock.Mock(), None, 302, "Found", {}, url)


if __name__ == "__main__":
    unittest.main()
