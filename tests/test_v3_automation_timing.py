from datetime import datetime, timedelta, timezone
import tempfile
import unittest

from app.v3_automation import ActionRegistry, AutomationEngine, AutomationStore
from app.v3_automation_timing import action_window, received_window_status, timing_envelope, validate_timing, window_status


class AutomationTimingTests(unittest.TestCase):
    def test_relative_deadline_is_exact_and_survives_serialized_restart(self):
        event = "2026-11-27T15:05:00Z"
        ready, deadline = action_window({"delay_seconds": 5, "within_seconds": 10}, event)
        start = datetime(2026, 11, 27, 15, 5, tzinfo=timezone.utc)
        self.assertEqual(deadline, start + timedelta(seconds=10))
        self.assertEqual("waiting_for_window", window_status(ready, deadline, start + timedelta(seconds=4)))
        self.assertEqual("ready", window_status(ready, deadline, start + timedelta(seconds=5)))
        self.assertEqual("window_missed", window_status(ready.isoformat(), deadline.isoformat(), start + timedelta(seconds=10)))
        self.assertEqual("window_missed", window_status(ready, deadline, start + timedelta(seconds=9), uncertainty_seconds=1))

    def test_day_boundary_uses_workflow_timezone_and_slot_does_not_roll_forward(self):
        ready, expiry = action_window({"end_of_day": True}, "2026-11-27T22:59:52Z", "Europe/Berlin")
        self.assertEqual("2026-11-27T23:00:00+00:00", expiry.isoformat())
        begin, end = action_window({"slot_start": "17:00", "slot_end": "19:00", "timezone": "Europe/Berlin"}, "2026-11-27T19:00:00Z")
        self.assertEqual("window_missed", window_status(begin, end, ready))
        with self.assertRaises(ValueError):
            action_window({"slot_start": "02:15", "slot_end": "04:00"}, "2026-03-29T00:00:00Z", "Europe/Berlin")
        with self.assertRaises(ValueError):
            validate_timing({"delay_seconds": True})

    def test_real_engine_skips_expired_action_and_logs_receipt_once_across_restart(self):
        with tempfile.TemporaryDirectory() as root:
            store = AutomationStore(root)
            rule = store.create("owner", {
                "name": "Timed test", "enabled": True, "trigger": {"type": "event", "name": "demo.completed"},
                "actions": [
                    {"type": "test.observable", "payload": {"label": "expired"}, "timing": {"within_seconds": 10}},
                    {"type": "test.observable", "payload": {"label": "still valid"}, "timing": {"within_seconds": 120}},
                ],
            })
            outcomes = []
            registry = ActionRegistry()
            registry.register("test.observable", lambda principal, payload, context: outcomes.append(payload["label"]), authorizer=lambda *_: True)
            time = datetime(2026, 11, 27, 15, 5, 11, tzinfo=timezone.utc)
            event = {"name": "demo.completed", "id": "copy-id", "occurred_at": "2026-11-27T15:05:00Z"}
            result = AutomationEngine(store, registry, clock=lambda: time).execute(
                rule, event, principal="owner", correlation_id="copy-id",
            )
            self.assertEqual("window_missed", result.status)
            self.assertEqual(["still valid"], outcomes)
            self.assertEqual(["window_missed", "executed"], [a["state"] for a in store.logs("owner")[0]["action_states"]])
            self.assertEqual(
                "duplicate",
                AutomationEngine(AutomationStore(root), registry, clock=lambda: time).execute(
                    rule, event, principal="owner", correlation_id="copy-id",
                ).status,
            )

    def test_early_action_never_executes_inline_and_may_be_rechecked(self):
        with tempfile.TemporaryDirectory() as root:
            store = AutomationStore(root)
            rule = store.create("owner", {
                "name": "Delayed test", "enabled": True, "trigger": {"type": "event"},
                "actions": [{"type": "test.observable", "timing": {"delay_seconds": 5, "within_seconds": 10}}],
            })
            registry = ActionRegistry()
            registry.register("test.observable", lambda *_: self.fail("early action"), authorizer=lambda *_: True)
            now = datetime(2026, 11, 27, 15, 5, tzinfo=timezone.utc)
            result = AutomationEngine(store, registry, clock=lambda: now).execute(
                rule, {"name": "demo.completed", "occurred_at": now.isoformat()}, principal="owner",
            )
            self.assertEqual("waiting_for_window", result.status)
            self.assertEqual([], store.logs("owner"))

    def test_transmitted_berlin_deadline_is_not_reinterpreted_in_new_york(self):
        envelope = timing_envelope({"end_of_day": True, "timezone": "Europe/Berlin"},
                                   "2026-11-27T23:58:00+01:00")
        self.assertEqual("Europe/Berlin", envelope["origin_timezone"])
        self.assertEqual("2026-11-27T22:58:00+00:00", envelope["requested_at"])
        self.assertEqual("2026-11-27T23:00:00+00:00", envelope["expires_at"])
        self.assertEqual("ready", received_window_status(envelope, "2026-11-27T17:59:00-05:00"))
        self.assertEqual("window_missed", received_window_status(envelope, "2026-11-27T18:00:00-05:00"))
        self.assertEqual("window_missed", received_window_status(envelope, "2026-11-27T18:05:00-05:00"))
        with self.assertRaises(ValueError):
            received_window_status({**envelope, "expires_at": "2027-01-01T00:00:00Z"}, "2026-11-27T22:59:00Z")
