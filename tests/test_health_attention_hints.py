"""Attention hints for unhealthy checks (no writes)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.health_attention_hints import suggest_unhealthy_checks


def _check(name: str, status: str, required: bool = False,
           message: str = ""):
    return {
        "name": name,
        "status": status,
        "required": required,
        "message": message,
    }


class HealthAttentionHintsTest(unittest.TestCase):
    def test_required_first(self) -> None:
        rows = [
            _check("Optional", "degraded", required=False),
            _check("Kern", "unavailable", required=True, message="Timeout"),
            _check("Ok", "healthy", required=True),
        ]
        proposals = suggest_unhealthy_checks(rows)
        self.assertEqual(
            [row["name"] for row in proposals], ["Kern", "Optional"])
        self.assertIn("Timeout", proposals[0]["reason"])

    def test_skips_healthy(self) -> None:
        rows = [_check("A", "healthy"), _check("B", "HEALTHY")]
        self.assertEqual(suggest_unhealthy_checks(rows), [])

    def test_accepts_objects(self) -> None:
        rows = [SimpleNamespace(name="Platte", status="degraded",
                                required=False, message="")]
        proposals = suggest_unhealthy_checks(rows)
        self.assertEqual(len(proposals), 1)
        self.assertIn("Platte", proposals[0]["reason"])

    def test_limits_items(self) -> None:
        rows = [_check(f"C{i}", "unknown") for i in range(7)]
        self.assertEqual(len(suggest_unhealthy_checks(rows)), 5)


if __name__ == "__main__":
    unittest.main()
