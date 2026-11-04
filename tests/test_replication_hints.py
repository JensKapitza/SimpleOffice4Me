"""Review hints for replication entries (no writes)."""

from __future__ import annotations

import unittest

from app.replication_hints import suggest_replication_review


def _target(target_id: str, label: str, enabled: bool = True,
            last_error: str = ""):
    return {
        "target_id": target_id,
        "label": label,
        "enabled": enabled,
        "last_error": last_error,
    }


def _rule(rule_id: str, label: str, last_run=None):
    return {"rule_id": rule_id, "label": label, "last_run": last_run}


class ReplicationHintsTest(unittest.TestCase):
    def test_error_target_first(self) -> None:
        status = {
            "targets": [_target("a", "Platte", last_error="Ziel offline")],
            "rules": [_rule("r", "Nachts")],
        }
        proposals = suggest_replication_review(status)
        self.assertEqual(len(proposals), 2)
        self.assertIn("Fehler", proposals[0]["reason"])
        self.assertIn("noch nie gelaufen", proposals[1]["reason"])

    def test_skips_paused_and_healthy(self) -> None:
        status = {
            "targets": [
                _target("a", "Pause", enabled=False,
                         last_error="Ziel offline"),
                _target("b", "Ok"),
            ],
            "rules": [_rule("r", "Lief", last_run="2026-11-01")],
        }
        self.assertEqual(suggest_replication_review(status), [])

    def test_skips_garbage(self) -> None:
        self.assertEqual(suggest_replication_review(None), [])
        self.assertEqual(suggest_replication_review({}), [])
        self.assertEqual(suggest_replication_review({"targets": [None]}), [])

    def test_limits_items(self) -> None:
        status = {
            "targets": [_target(str(i), f"Z{i}", last_error="x")
                        for i in range(7)],
            "rules": [],
        }
        self.assertEqual(len(suggest_replication_review(status)), 5)


if __name__ == "__main__":
    unittest.main()
