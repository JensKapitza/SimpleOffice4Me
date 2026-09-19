import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from simpleoffice_mini_control import ControlStore
from simpleoffice_mini_core import write_status
from tools.mini_services import main, service_command


class ServiceCliTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "mini.json"
        write_status({"state": "running", "services": {"dns": {"id": "dns", "state": "running"}}}, self.path)
        self.store = ControlStore(self.path)

    def test_worker_consumes_same_mailbox_and_cli_reports_result(self):
        def consume(_delay):
            command = self.store.claim()
            self.assertEqual(("dns", "restart"), (command["service"], command["action"]))
            self.store.finish(command["id"], True, {"state": "running"})
        with patch("tools.mini_services.time.sleep", side_effect=consume):
            result, code = service_command(self.path, "dns", "restart")
        self.assertEqual(0, code)
        self.assertEqual("completed", result["state"])
        self.assertEqual({"state": "running"}, result["result"])

    def test_timeout_preserves_pending_command_and_deduplicates(self):
        first, code = service_command(self.path, "dns", "start", wait_seconds=0)
        self.assertEqual(3, code)
        second, code = service_command(self.path, "dns", "start", wait_seconds=0)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual("queued", self.store.operation(first["id"])["state"])
        self.store.finish(first["id"], False, {"message": "Missing permission"})
        result, code = service_command(self.path, "dns", "status", operation_id=first["id"])
        self.assertEqual(1, code)
        self.assertEqual("failed", result["state"])
        self.assertEqual(1, service_command(self.path, "sip", "status", operation_id=first["id"])[1])

    def test_stale_worker_never_receives_new_commands_or_reports_running(self):
        with patch("tools.mini_services.read_status", return_value={"state": "running", "stale": True, "services": {"dns": {"state": "running"}}}):
            self.assertEqual(3, service_command(self.path, "dns", "start", wait_seconds=0)[1])
            self.assertEqual("unavailable", service_command(self.path, "dns", "status")[0]["state"])
        self.assertIsNone(self.store.claim())

    def test_cli_single_stop_never_stops_or_instantiates_worker(self):
        output = io.StringIO()
        with patch("tools.mini_services.Worker") as worker, patch("tools.service_control.stop") as stop, redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                main(["stop", "--service", "dns", "--wait", "0", "--config", str(self.path)])
        self.assertEqual(3, raised.exception.code)
        self.assertEqual("queued", json.loads(output.getvalue())["state"])
        worker.assert_not_called()
        stop.assert_not_called()

    def test_invalid_cli_never_falls_back_to_worker_start(self):
        for arguments in (["--service", "unknown"], ["--service", "dns", "--wait", "61"], ["start", "--operation", "id"]):
            with self.subTest(arguments=arguments), patch("tools.mini_services.Worker") as worker, patch("sys.stderr", new=io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main(arguments)
                self.assertEqual(2, raised.exception.code)
                worker.assert_not_called()

    def test_cli_storage_error_hides_exception_contents(self):
        output = io.StringIO()
        with patch("tools.mini_services.ControlStore", side_effect=PermissionError("private path")), redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                main(["start", "--service", "dns", "--config", str(self.path)])
        self.assertEqual(1, raised.exception.code)
        self.assertNotIn("private path", output.getvalue())
        self.assertEqual("permission_denied", json.loads(output.getvalue())["error"]["code"])
