from __future__ import annotations

import unittest

from app.v3_capabilities import enabled, run_if_enabled, snapshot, state


class V3CapabilityTests(unittest.TestCase):
    def test_all_v3_capabilities_are_disabled_by_default(self):
        clean = {}
        rows = snapshot(clean)
        self.assertGreaterEqual(len(rows), 10)
        self.assertTrue(all(row["known"] for row in rows))
        self.assertTrue(all(not row["enabled"] for row in rows))

    def test_flag_is_server_side_and_reversible(self):
        env = {"SIMPLEOFFICE_V3_SAMPLE_ENABLED": "true"}
        self.assertTrue(enabled("v3.sample", env))
        self.assertFalse(enabled("v3.sample", {}))
        self.assertFalse(enabled("v3.sample", {"SIMPLEOFFICE_V3_SAMPLE_ENABLED": "0"}))

    def test_unknown_optional_capability_fails_closed_without_exception(self):
        self.assertFalse(enabled("v3.does-not-exist", {}))
        self.assertEqual(False, state("v3.does-not-exist", {})["enabled"])
        self.assertFalse(state("v3.does-not-exist", {})["known"])

    def test_disabled_capability_uses_fallback_and_does_not_execute_operation(self):
        calls = []
        result = run_if_enabled(
            "v3.sample",
            lambda: calls.append("operation"),
            fallback=lambda: "legacy",
            environ={},
        )
        self.assertEqual("legacy", result)
        self.assertEqual([], calls)

    def test_diagnostics_do_not_expose_environment_values(self):
        secretish = "do-not-render-this-value"
        rows = snapshot({"SIMPLEOFFICE_V3_SAMPLE_ENABLED": secretish})
        self.assertNotIn(secretish, repr(rows))


if __name__ == "__main__":
    unittest.main()
