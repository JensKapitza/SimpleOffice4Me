from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app import mail_case_federation_runtime as runtime


class MailCaseFederationRuntimeTests(unittest.TestCase):
    def app(self):
        return SimpleNamespace(
            testing=False, config={"DOCUMENT_ROOT": "initial-root"},
            app_context=nullcontext, logger=Mock(),
        )

    def test_worker_never_follows_a_reconfigured_document_root(self):
        app = self.app()
        def change_root(_seconds):
            app.config["DOCUMENT_ROOT"] = "other-root"
        with patch.object(runtime, "_STARTED", True), \
                patch.object(runtime, "_enabled", return_value=True), \
                patch.object(runtime, "retry_due_mail_case_events", return_value={"sent": 0, "failed": 0}) as retry, \
                patch.object(runtime.time, "sleep", side_effect=change_root):
            runtime._worker(app, "initial-root")
            retry.assert_called_once()
            self.assertEqual("initial-root", retry.call_args.args[0])
            self.assertFalse(runtime._STARTED)

    def test_worker_exits_without_accessing_storage_in_testing_mode(self):
        app = self.app()
        app.testing = True
        with patch.object(runtime, "_STARTED", True), \
                patch.object(runtime, "retry_due_mail_case_events") as retry:
            runtime._worker(app, "initial-root")
            retry.assert_not_called()
            self.assertFalse(runtime._STARTED)

    def test_disabling_worker_stops_retries_and_allows_later_restart(self):
        app = self.app()
        with patch.object(runtime, "_STARTED", True), \
                patch.object(runtime, "_enabled", side_effect=[True, False]), \
                patch.object(runtime, "retry_due_mail_case_events", return_value={"sent": 0, "failed": 0}) as retry, \
                patch.object(runtime.time, "sleep"):
            runtime._worker(app, "initial-root")
            retry.assert_called_once()
            self.assertFalse(runtime._STARTED)

    def test_start_captures_document_root_before_thread_runs(self):
        app = self.app()
        with patch.object(runtime, "_STARTED", False), \
                patch.object(runtime, "_enabled", return_value=True), \
                patch.object(runtime.threading, "Thread") as thread:
            runtime._start(app)
            app.config["DOCUMENT_ROOT"] = "other-root"
            self.assertEqual((app, "initial-root"), thread.call_args.kwargs["args"])
            thread.return_value.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
