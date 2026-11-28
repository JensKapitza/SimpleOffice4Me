from __future__ import annotations

import hashlib
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.todo_store import TodoStore
from app.v3_automation import ActionRegistry, AutomationEngine, AutomationStore, MAX_DEPTH, default_action_registry


class V3AutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = AutomationStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def rule(self, **overrides):
        values = {
            "name": "Overdue invoice",
            "enabled": True,
            "scope": {},
            "trigger": {"type": "event", "name": "finance.invoice.overdue"},
            "conditions": [{"field": "event.overdue", "op": "eq", "value": True}],
            "actions": [{"type": "task.create", "payload": {"title": "Rechnung prüfen"}}],
        }
        values.update(overrides)
        return self.store.create("alice", values)

    def test_real_task_action_is_idempotent(self):
        rule = self.rule()
        engine = AutomationEngine(self.store, default_action_registry(self.root))
        event = {"id": "invoice-1", "name": "finance.invoice.overdue", "overdue": True}
        first = engine.execute(rule, event, principal="alice", correlation_id="invoice-1")
        second = engine.execute(rule, event, principal="alice", correlation_id="invoice-1")
        self.assertEqual("succeeded", first.status)
        self.assertEqual("duplicate", second.status)
        self.assertEqual(1, len(TodoStore(self.root).items("alice")))

    def test_task_receipt_prevents_duplicate_after_execution_log_failure(self):
        rule = self.rule(trigger={"type": "check", "name": "manual.invoice.check"})
        engine = AutomationEngine(self.store, default_action_registry(self.root))
        event = {"id": "run-token-hash", "name": "manual.invoice.check", "overdue": True}
        record = self.store.record_execution
        with patch.object(self.store, "record_execution", side_effect=OSError("synthetic log failure")):
            with self.assertRaises(OSError):
                engine.execute(rule, event, principal="alice", trigger_type="check", correlation_id="run-1")
        with patch.object(self.store, "record_execution", side_effect=record):
            result = engine.execute(rule, event, principal="alice", trigger_type="check", correlation_id="run-1")
        self.assertEqual("succeeded", result.status)
        self.assertEqual(1, len(TodoStore(self.root).items("alice")))

    def test_concurrent_same_run_creates_one_task(self):
        rule = self.rule()
        barrier = threading.Barrier(2)
        registry = ActionRegistry()

        def create_task(principal, payload, context):
            barrier.wait(timeout=5)
            return TodoStore(self.root).add(
                payload["title"], principal, payload.get("values", {}),
                automation_operation_id=context["automation_operation_id"],
            )

        registry.register("task.create", create_task, authorizer=lambda _principal, _payload: True)
        engine = AutomationEngine(self.store, registry)
        event = {"id": "same-event", "name": "finance.invoice.overdue", "overdue": True}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda _: engine.execute(rule, event, principal="alice", correlation_id="same-run"),
                range(2),
            ))
        self.assertTrue(all(result.status == "succeeded" for result in results))
        self.assertEqual(1, len(TodoStore(self.root).items("alice")))

    def test_dry_run_plans_without_mutation(self):
        rule = self.rule()
        result = AutomationEngine(self.store, default_action_registry(self.root)).execute(
            rule,
            {"name": "finance.invoice.overdue", "overdue": True},
            principal="alice",
            dry_run=True,
        )
        self.assertEqual("dry_run", result.status)
        self.assertEqual(1, len(result.actions))
        self.assertEqual([], TodoStore(self.root).items("alice"))

    def test_conditions_and_principal_fail_closed(self):
        rule = self.rule()
        engine = AutomationEngine(self.store, default_action_registry(self.root))
        no_match = engine.execute(
            rule,
            {"name": "finance.invoice.overdue", "overdue": False},
            principal="alice",
        )
        denied = engine.execute(
            rule,
            {"name": "finance.invoice.overdue", "overdue": True},
            principal="bob",
        )
        self.assertEqual("not_matched", no_match.status)
        self.assertEqual("denied", denied.status)

    def test_incompatible_contains_condition_returns_not_matched(self):
        rule = self.rule(conditions=[{"field": "event.label", "op": "contains", "value": []}])
        result = AutomationEngine(self.store, default_action_registry(self.root)).execute(
            rule,
            {"name": "finance.invoice.overdue", "label": "invoice"},
            principal="alice",
            dry_run=True,
        )
        self.assertEqual("not_matched", result.status)

    def test_action_authorizer_blocks_mutation(self):
        registry = ActionRegistry()
        called = []
        registry.register(
            "blocked",
            lambda principal, payload, context: called.append(payload),
            authorizer=lambda principal, payload: False,
        )
        rule = self.rule(actions=[{"type": "blocked", "payload": {}}])
        result = AutomationEngine(self.store, registry).execute(
            rule,
            {"name": "finance.invoice.overdue", "overdue": True},
            principal="alice",
        )
        self.assertEqual("denied", result.status)
        self.assertEqual([], called)

    def test_recursion_is_bounded(self):
        rule = self.rule()
        result = AutomationEngine(self.store, default_action_registry(self.root)).execute(
            rule,
            {"name": "finance.invoice.overdue", "overdue": True},
            principal="alice",
            depth=MAX_DEPTH,
        )
        self.assertEqual("blocked", result.status)

    def test_update_increments_version_and_disable_preserves_rule(self):
        rule = self.rule()
        updated = self.store.update(rule.rule_id, "alice", {"enabled": False})
        self.assertFalse(updated.enabled)
        self.assertEqual(rule.version + 1, updated.version)
        self.assertEqual(rule.rule_id, self.store.get(rule.rule_id, "alice").rule_id)

    def test_peer_outcomes_use_multiple_batches_and_prefer_confirmed_delivery(self):
        from app.v3_automation import ExecutionResult

        rule = self.rule()
        keys = [hashlib.sha256(f"synthetic receipt {n}".encode()).hexdigest() for n in range(21)]
        for key in keys:
            self.store.record_execution(
                rule, key=f"delivered:{key}", correlation_id=key, principal="alice",
                result=ExecutionResult("delivered", True, False, ()),
            )
        self.store.record_execution(
            rule, key=f"window_missed:{keys[0]}", correlation_id=keys[0], principal="alice",
            result=ExecutionResult("window_missed", True, False, ()),
        )
        self.assertEqual(dict.fromkeys(keys, "delivered"), self.store.recent_peer_outcomes("alice", keys))
        self.assertEqual({}, self.store.recent_peer_outcomes("bob", keys))

    def test_confirmation_is_owner_bound_and_expires(self):
        rule = self.rule(trigger={"type": "check", "name": "manual.check"})
        token = "synthetic-confirmation-token"
        expires_at = self.store.create_confirmation(
            rule, {"name": "manual.check", "approved": True},
            principal="alice", token=token,
        )
        self.assertGreater(expires_at, "")
        bound = self.store.get_confirmation(token, "alice")
        self.assertEqual(rule.version, bound["rule_version"])
        self.assertEqual({"name": "manual.check", "approved": True}, bound["event"])
        self.assertIsNone(self.store.get_confirmation(token, "bob"))
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.store._db() as db:
            db.execute(
                "UPDATE v3_automation_confirmation SET expires_at='2000-01-01T00:00:00Z' WHERE token_hash=?",
                (token_hash,),
            )
        self.assertIsNone(self.store.get_confirmation(token, "alice"))


if __name__ == "__main__":
    unittest.main()
