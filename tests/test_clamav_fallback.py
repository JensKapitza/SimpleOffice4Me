import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.attachment_security import ClamAV


class _Completed:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class ClamAVFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "sample.bin"
        self.path.write_bytes(b"sample")

    def tearDown(self):
        self.temp.cleanup()

    def test_auto_selected_clamdscan_falls_back_to_clamscan_on_operational_error(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command[0])
            if Path(command[0]).name == "clamdscan":
                return _Completed(2, stderr="Could not connect to clamd socket")
            return _Completed(0, stdout=f"{self.path}: OK")

        with patch.dict(os.environ, {"SIMPLEOFFICE_CLAMAV_SCANNER": ""}, clear=False), \
             patch("app.attachment_security.shutil.which", side_effect=lambda name: f"/usr/bin/{name}" if name in {"clamdscan", "clamscan"} else None), \
             patch("app.attachment_security.subprocess.run", side_effect=fake_run):
            result = ClamAV().scan(self.path)

        self.assertEqual("clean", result.verdict)
        self.assertEqual("clamscan", result.engine)
        self.assertEqual(["/usr/bin/clamdscan", "/usr/bin/clamscan"], calls)

    def test_malware_verdict_never_falls_back(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command[0])
            return _Completed(1, stdout=f"{self.path}: Eicar-Test-Signature FOUND")

        with patch.dict(os.environ, {"SIMPLEOFFICE_CLAMAV_SCANNER": ""}, clear=False), \
             patch("app.attachment_security.shutil.which", side_effect=lambda name: f"/usr/bin/{name}" if name in {"clamdscan", "clamscan"} else None), \
             patch("app.attachment_security.subprocess.run", side_effect=fake_run):
            result = ClamAV().scan(self.path)

        self.assertEqual("infected", result.verdict)
        self.assertEqual(["/usr/bin/clamdscan"], calls)

    def test_explicit_scanner_configuration_does_not_fallback(self):
        explicit = Path(self.temp.name) / "clamdscan"
        explicit.write_text("", encoding="utf-8")
        with patch.dict(os.environ, {"SIMPLEOFFICE_CLAMAV_SCANNER": str(explicit)}, clear=False), \
             patch("app.attachment_security.subprocess.run", return_value=_Completed(2, stderr="socket unavailable")), \
             patch("app.attachment_security.shutil.which", return_value="/usr/bin/clamscan"):
            with self.assertRaises(RuntimeError):
                ClamAV().scan(self.path)

    def test_timeout_from_auto_clamdscan_can_fallback(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command[0])
            if Path(command[0]).name == "clamdscan":
                raise subprocess.TimeoutExpired(command, 5)
            return _Completed(0, stdout=f"{self.path}: OK")

        with patch.dict(os.environ, {"SIMPLEOFFICE_CLAMAV_SCANNER": ""}, clear=False), \
             patch("app.attachment_security.shutil.which", side_effect=lambda name: f"/usr/bin/{name}" if name in {"clamdscan", "clamscan"} else None), \
             patch("app.attachment_security.subprocess.run", side_effect=fake_run):
            result = ClamAV(timeout=5).scan(self.path)

        self.assertEqual("clean", result.verdict)
        self.assertEqual(["/usr/bin/clamdscan", "/usr/bin/clamscan"], calls)


if __name__ == "__main__":
    unittest.main()
