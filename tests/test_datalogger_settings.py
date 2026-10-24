"""Global data-logger worker interval storage and runtime behavior."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.datalogger_store import DataLoggerStore
from tools import datalogger_worker
from tools.datalogger_worker import run


class DataLoggerPollIntervalTests(unittest.TestCase):
    def test_legacy_environment_is_used_until_persistent_setting_is_saved(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ, {"SIMPLEOFFICE_DATALOGGER_TICK_SECONDS": "17"},
        ):
            store = DataLoggerStore(root)
            self.assertEqual(17, store.poll_interval_seconds())
            self.assertEqual(12, store.update_poll_interval("12", "admin"))
            self.assertEqual(12, DataLoggerStore(root).poll_interval_seconds())
            with store.connect() as db:
                event = db.execute(
                    "SELECT action,detail FROM metric_event ORDER BY event_id DESC LIMIT 1",
                ).fetchone()
            self.assertEqual("poll_interval_updated", event["action"])
            self.assertEqual('{"poll_interval_seconds": 12}', event["detail"])

    def test_invalid_values_are_rejected_without_changing_persisted_setting(self):
        with tempfile.TemporaryDirectory() as root:
            store = DataLoggerStore(root)
            store.update_poll_interval(9, "admin")
            for value in (True, 1.5, "invalid", 0, 61):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    store.update_poll_interval(value, "admin")
            self.assertEqual(9, store.poll_interval_seconds())

    def test_invalid_legacy_value_defaults_and_out_of_range_value_is_clamped(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ, {"SIMPLEOFFICE_DATALOGGER_TICK_SECONDS": "invalid"},
        ):
            self.assertEqual(5, DataLoggerStore(root).poll_interval_seconds())
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ, {"SIMPLEOFFICE_DATALOGGER_TICK_SECONDS": "120"},
        ):
            self.assertEqual(60, DataLoggerStore(root).poll_interval_seconds())

    def test_worker_uses_persisted_interval_for_its_next_sleep(self):
        with tempfile.TemporaryDirectory() as root:
            DataLoggerStore(root).update_poll_interval(13, "admin")

            def stop_after_sleep(seconds):
                self.assertEqual(13, seconds)
                datalogger_worker.running = False

            with patch("tools.datalogger_worker.running", True), patch(
                "tools.datalogger_worker.time.sleep", side_effect=stop_after_sleep,
            ) as sleep:
                run(root)
            sleep.assert_called_once_with(13)


if __name__ == "__main__":
    unittest.main()
