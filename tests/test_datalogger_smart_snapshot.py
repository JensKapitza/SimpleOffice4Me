import json
import subprocess
import sys
import unittest

from tools.datalogger_smart_snapshot import MAX_BYTES, normalize


class SmartSnapshotTests(unittest.TestCase):
    def test_projects_only_supported_metrics(self):
        self.assertEqual(normalize({
            "smartctl": {"exit_status": 0, "output": ["private"]},
            "serial_number": "private-serial",
            "smart_status": {"passed": True},
            "temperature": {"current": 33},
            "power_on_time": {"hours": 654},
        }), {"healthy": 1, "temperature_c": 33, "power_on_hours": 654})

    def test_failed_disk_is_a_real_measurement(self):
        self.assertEqual(normalize({"smartctl": {"exit_status": 8}}), {"healthy": 0})

    def test_transport_failure_and_unexpected_numbers_are_not_measurements(self):
        for code in (None, False, -1, 2, 7, 256):
            with self.subTest(code=code), self.assertRaises(ValueError):
                normalize({"smartctl": {"exit_status": code}, "temperature": {"current": 33}})
        with self.assertRaises(ValueError):
            normalize({"smartctl": {"exit_status": 0}, "temperature": {"current": True}})

    def test_cli_rejects_large_input_and_does_not_print_it(self):
        for payload, code in ((b"x" * (MAX_BYTES + 1), 1),
                              (b'{"smartctl":{"exit_status":0},"temperature":{"current":33}}', 0)):
            with self.subTest(code=code):
                result = subprocess.run([sys.executable, "-m", "tools.datalogger_smart_snapshot"],
                                        input=payload, capture_output=True, check=False)
                self.assertEqual(result.returncode, code)
                if not code:
                    self.assertEqual(json.loads(result.stdout), {"temperature_c": 33})
                else:
                    self.assertEqual(result.stdout, b"")
                    self.assertLess(len(result.stderr), 100)
