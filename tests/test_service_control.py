import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import service_control


class ServiceControlTests(unittest.TestCase):
    def test_register_uses_private_atomic_record_and_unregisters_own_pid(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(service_control, "RUN_DIR", Path(temp)):
            service_control.register("web", os.getpid(), "test_service_control")
            record = service_control.read("web")
            self.assertEqual(os.getpid(), record["pid"])
            self.assertEqual("test_service_control", record["marker"])
            with patch("tools.service_control._command_line", return_value="python test_service_control"):
                self.assertTrue(service_control.process_matches(record))
            self.assertEqual(0o600, (Path(temp) / "web.json").stat().st_mode & 0o777)
            service_control.unregister("web", os.getpid())
            self.assertIsNone(service_control.read("web"))

    @unittest.skipIf(os.name == "nt", "POSIX signal behavior")
    def test_stop_terminates_only_a_matching_registered_process(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(service_control, "RUN_DIR", Path(temp)):
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "simpleoffice-test-marker"])
            try:
                service_control.register("index", child.pid, "simpleoffice-test-marker")
                with patch("tools.service_control.process_matches", side_effect=[True, False, False]):
                    self.assertTrue(service_control.stop(timeout=5))
                child.wait(timeout=5)
                self.assertIsNone(service_control.read("index"))
            finally:
                if child.poll() is None:
                    child.kill()

    def test_stale_or_reused_pid_is_not_signalled(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(service_control, "RUN_DIR", Path(temp)):
            service_control.register("web", os.getpid(), "marker-that-is-not-in-this-process")
            real_kill = os.kill
            calls = []
            def safe_kill(pid, sig):
                calls.append((pid, sig))
                return real_kill(pid, sig)
            with patch("tools.service_control.os.kill", side_effect=safe_kill):
                self.assertTrue(service_control.stop(timeout=1))
            self.assertNotIn((os.getpid(), signal.SIGTERM), calls)
