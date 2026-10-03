import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import sftp_service
from tools import service_control


class SftpStartupCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        context = patch.object(service_control, "RUN_DIR", self.root)
        context.start()
        self.addCleanup(context.stop)

    def child(self, ignore_stop=False):
        script = "import signal,time; "
        if ignore_stop:
            script += "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        script += "print('ready',flush=True); time.sleep(60)"
        child = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
        self.addCleanup(self.dispose, child)
        self.assertEqual("ready\n", child.stdout.readline())
        return child

    @staticmethod
    def dispose(child):
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        if child.stdout:
            child.stdout.close()

    def start_with(self, child, *, record=None, readiness=None):
        read_record = service_control.read
        startup_records = [None, record]
        def read(role):
            return startup_records.pop(0) if startup_records else read_record(role)
        with patch.object(sftp_service.sftp_setup, "initialize"), \
                patch.object(sftp_service.subprocess, "Popen", return_value=child), \
                patch.object(service_control, "read", side_effect=read), \
                patch.object(sftp_service.time, "monotonic", side_effect=[0, 0, 9]), \
                patch.object(sftp_service, "_ready", side_effect=readiness):
            return sftp_service._start(dict(sftp_service.DEFAULTS), self.root)

    def test_timeout_reaps_child_and_removes_its_service_record(self):
        child = self.child()
        service_control.register("sftp", child.pid, "test-startup")
        with self.assertRaisesRegex(RuntimeError, "SFTP startet nicht"):
            self.start_with(child)
        self.assertIsNotNone(child.returncode)
        self.assertIsNone(service_control.read("sftp"))

    @unittest.skipIf(os.name == "nt", "Windows terminate forcibly ends the process")
    def test_timeout_escalates_when_child_ignores_termination(self):
        child = self.child(ignore_stop=True)
        with self.assertRaisesRegex(RuntimeError, "SFTP startet nicht"):
            self.start_with(child)
        self.assertIsNotNone(child.returncode)

    def test_readiness_exception_reaps_child_but_preserves_another_record(self):
        child = self.child()
        service_control.register("sftp", os.getpid(), "other-owner")
        with self.assertRaisesRegex(ValueError, "probe failed"):
            self.start_with(child, record={"pid": child.pid}, readiness=ValueError("probe failed"))
        self.assertIsNotNone(child.returncode)
        self.assertEqual(os.getpid(), service_control.read("sftp")["pid"])

    def test_successful_start_keeps_child_running(self):
        child = self.child()
        self.start_with(child, record={"pid": child.pid}, readiness=[True])
        self.assertIsNone(child.poll())

    def test_already_exited_child_leaves_no_stale_registration(self):
        child = self.child()
        service_control.register("sftp", child.pid, "test-startup")
        child.kill()
        child.wait(timeout=5)
        sftp_service._cleanup_failed_start(child)
        self.assertIsNone(service_control.read("sftp"))

    def test_existing_service_is_never_spawned_or_terminated(self):
        child = self.child()
        with patch.object(service_control, "read", return_value={"pid": child.pid}), \
                patch.object(service_control, "process_matches", return_value=True), \
                patch.object(sftp_service.subprocess, "Popen") as spawn:
            sftp_service._start(dict(sftp_service.DEFAULTS), self.root)
        spawn.assert_not_called()
        self.assertIsNone(child.poll())
