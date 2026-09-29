from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
