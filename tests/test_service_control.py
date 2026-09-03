import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    def test_register_closes_raw_descriptor_if_fdopen_fails(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(service_control, "RUN_DIR", Path(temp)):
            with patch("tools.service_control.os.open", return_value=12345), patch(
                "tools.service_control.os.fdopen", side_effect=OSError("fdopen failed")
            ), patch("tools.service_control.os.close") as close:
                with self.assertRaisesRegex(OSError, "fdopen failed"):
                    service_control.register("web", os.getpid(), "marker")
                close.assert_called_once_with(12345)

    def test_register_never_raw_closes_after_fdopen_takes_ownership(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(service_control, "RUN_DIR", Path(temp)):
            handle = MagicMock()
            handle.__enter__.return_value = handle
            handle.__exit__.return_value = False
            handle.fileno.return_value = 12345
            with patch("tools.service_control.os.open", return_value=12345), patch(
                "tools.service_control.os.fdopen", return_value=handle
            ), patch("tools.service_control.os.fsync"), patch(
                "tools.service_control.os.replace", side_effect=OSError("replace failed")
            ), patch("tools.service_control.os.close") as close:
                with self.assertRaisesRegex(OSError, "replace failed"):
                    service_control.register("web", os.getpid(), "marker")
                close.assert_not_called()
                handle.__exit__.assert_called_once()

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

    def test_invalid_role_and_pid_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(service_control, "RUN_DIR", Path(temp)):
            with self.assertRaises(ValueError):
                service_control.register("unknown", os.getpid(), "marker")
            with self.assertRaises(ValueError):
                service_control.register("web", 0, "marker")
            with self.assertRaises(ValueError):
                service_control.read("../web")

    def test_nonpositive_pid_is_never_signalled(self):
        with patch("tools.service_control.os.kill") as kill:
            self.assertFalse(service_control.process_matches({"pid": 0, "marker": "marker"}))
            kill.assert_not_called()

    def test_invalid_state_records_are_ignored(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(service_control, "RUN_DIR", Path(temp)):
            path = Path(temp) / "web.json"
            path.write_text("not-json", encoding="utf-8")
            self.assertIsNone(service_control.read("web"))

            path.write_text("x" * (64 * 1024 + 1), encoding="utf-8")
            self.assertIsNone(service_control.read("web"))

            path.write_text(
                json.dumps({"version": 1, "role": "web", "pid": os.getpid(), "marker": "bad\u0000marker"}),
                encoding="utf-8",
            )
            self.assertIsNone(service_control.read("web"))

    @unittest.skipIf(os.name == "nt", "symlink semantics differ on Windows")
    def test_state_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(service_control, "RUN_DIR", Path(temp)):
            target = Path(temp) / "outside.json"
            target.write_text("{}", encoding="utf-8")
            state = Path(temp) / "web.json"
            state.symlink_to(target)
            self.assertIsNone(service_control.read("web"))
            with self.assertRaises(RuntimeError):
                service_control.register("web", os.getpid(), "marker")
