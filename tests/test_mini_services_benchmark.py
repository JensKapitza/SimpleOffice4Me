import unittest
from unittest.mock import patch

from tools.mini_services_benchmark import measure


class FakeService:
    def __init__(self):
        self.running = False
        self.payload = bytearray(2048)

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


class MiniServicesBenchmarkTest(unittest.TestCase):
    def test_report_includes_construction_and_python_heap_metrics(self):
        factories = {name: FakeService for name in ("dhcp", "dns", "tftp", "sip")}
        with patch("tools.mini_services_benchmark._service_factories", return_value=factories), patch(
            "tools.mini_services_benchmark.service_health",
            side_effect=lambda service: service.running,
        ):
            report = measure(2)

        self.assertEqual(2, report["iterations"])
        self.assertIn("no process cold start", report["scope"])
        self.assertEqual([], report["remaining_threads"])
        for service in report["services"].values():
            self.assertIn("construct_ms", service)
            self.assertIn("python_heap_peak_kib", service)
            self.assertGreaterEqual(service["python_heap_peak_kib"]["max"], 2)
            self.assertIn("start_ms", service)
            self.assertIn("stop_ms", service)
            self.assertIn("cpu_ms", service)

    def test_iteration_bounds_remain_enforced(self):
        for value in (0, 51, 1.5, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                measure(value)


if __name__ == "__main__":
    unittest.main()
