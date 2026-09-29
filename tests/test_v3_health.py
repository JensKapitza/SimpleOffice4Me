from __future__ import annotations

import time
import unittest

from app.v3_health import HealthRegistry, public_summary


class V3HealthTests(unittest.TestCase):
    def test_optional_not_configured_does_not_break_readiness(self):
        registry = HealthRegistry()
        registry.register(
            "core",
            lambda: {"status": "healthy", "code": "ok", "message": "ok"},
            required=True,
        )
        registry.register(
            "optional",
            lambda: {"status": "not_configured", "code": "disabled", "message": "disabled"},
        )

        report = registry.run()

        self.assertTrue(report["ready"])
        self.assertEqual("healthy", report["status"])
        self.assertEqual(
            {"healthy", "not_configured"},
            {item.status for item in report["checks"]},
        )

    def test_required_failure_marks_service_unready_without_exception_text(self):
        registry = HealthRegistry()

        def broken():
            raise RuntimeError("/private/path SECRET=do-not-leak")

        registry.register("database", broken, required=True)
        report = registry.run()
        row = report["checks"][0]

        self.assertFalse(report["ready"])
        self.assertEqual("unavailable", report["status"])
        self.assertEqual("check_failed", row.code)
        self.assertNotIn("SECRET", row.message)
        self.assertNotIn("/private", row.message)

    def test_timeout_is_bounded(self):
        registry = HealthRegistry()

        def slow():
            time.sleep(0.25)
            return {"status": "healthy", "code": "late", "message": "late"}

        registry.register("slow", slow, required=True, timeout_seconds=0.02)
        started = time.monotonic()
        report = registry.run(total_timeout_seconds=0.05)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.15)
        self.assertFalse(report["ready"])
        self.assertEqual("check_timeout", report["checks"][0].code)

    def test_optional_failure_degrades_but_does_not_make_service_unready(self):
        registry = HealthRegistry()
        registry.register(
            "core",
            lambda: {"status": "healthy", "code": "ok", "message": "ok"},
            required=True,
        )
        registry.register(
            "optional",
            lambda: {"status": "unavailable", "code": "down", "message": "down"},
        )

        report = registry.run()

        self.assertTrue(report["ready"])
        self.assertEqual("degraded", report["status"])

    def test_public_summary_omits_component_details(self):
        registry = HealthRegistry()
        registry.register(
            "database",
            lambda: {
                "status": "healthy",
                "code": "database_ok",
                "message": "internal detail",
                "metrics": {"free_mib": 123},
            },
            required=True,
        )
        report = registry.run()

        payload = public_summary(report)

        self.assertEqual({"status", "live", "ready", "checked_at"}, set(payload))
        self.assertNotIn("checks", payload)


if __name__ == "__main__":
    unittest.main()
