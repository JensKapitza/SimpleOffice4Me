import hashlib
import importlib.util
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "install_invoice_validator.py"
SPEC = importlib.util.spec_from_file_location("install_invoice_validator", SCRIPT)
installer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(installer)


class InvoiceValidatorInstallerTests(unittest.TestCase):
    def test_missing_sha256_sidecar_falls_back_to_published_sha1(self):
        payload = b"executable validator jar"

        def download(url):
            if url.endswith(".sha256"):
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            if url.endswith(".sha1"):
                return hashlib.sha1(payload).hexdigest().encode("ascii")
            return payload

        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / installer.FILENAME
            checksum_file = target.with_suffix(".jar.sha256")
            with mock.patch.object(installer, "TARGET", target), \
                 mock.patch.object(installer, "CHECKSUM_FILE", checksum_file), \
                 mock.patch.object(installer, "_download", side_effect=download), \
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
                 mock.patch.object(installer, "_download", side_effect=[b"0" * 64, b"payload"]), \
                 mock.patch.object(installer.shutil, "which", return_value="/usr/bin/java"):
                with self.assertRaisesRegex(RuntimeError, "checksum does not match"):
                    installer.install()
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
