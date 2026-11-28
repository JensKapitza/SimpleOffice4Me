from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.document_copy_events import JOB_EVENT_NAME
from app.replication_store import ReplicationStore
from app.settings_store import SettingsStore
from app.v3_activity import ActivityStore
from app.v3_automation import ActionRegistry, AutomationEngine, AutomationStore


class ReplicationCopyEventTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        home = Path(temporary.name)
        self.root = home / "data"
        self.root.mkdir()
        self.store = ReplicationStore(self.root)
        SettingsStore(self.root).save({"v3": {"v3.activity": True}}, "owner")
        self.notes = self.store.control / "notes.json"
        self.notes.write_text("synthetic content", encoding="utf-8")
        self.targets = []
        self.rules = []
        for label in ("first", "second"):
            destination = home / label
            destination.mkdir()
            self.targets.append(destination)
            target = self.store.add_target({"label": label, "path": str(destination)}, "owner")
            rule = self.store.add_rule({
                "label": label, "target_id": target["target_id"], "categories": ["notes"],
            }, "owner")
            self.rules.append(rule["rule_id"])

    def receipts(self):
        return ActivityStore(self.root).list(event_type=JOB_EVENT_NAME, actor="owner")

    def test_run_all_records_one_completion_after_two_verified_copies(self):
        result = self.store.run_all("owner", run_id="timer-1")
        self.assertEqual([], result["errors"])
        self.assertEqual([1, 1], [item["copied"] for item in result["completed"]])
        for destination in self.targets:
            self.assertEqual(
                "synthetic content",
                (destination / "SimpleOffice-Spiegelung/control/notes.json").read_text("utf-8"),
            )
        first, = self.receipts()
        self.assertEqual({"files_copied": 2, "rules_completed": 2}, first.metadata)
        self.assertEqual("replication.batch", first.source)
        for destination in self.targets:
            (destination / "SimpleOffice-Spiegelung/control/notes.json").unlink()
        self.store.run_all("owner", run_id="timer-1")
        self.assertEqual([first.event_id], [item.event_id for item in self.receipts()])
        self.store.run_all("owner", run_id="timer-2")
        self.assertEqual([first.event_id], [item.event_id for item in self.receipts()])

    def test_partial_batch_has_no_receipt_and_recovery_can_emit_one(self):
        self.targets[1].rmdir()
        result = self.store.run_all("owner", run_id="timer-1")
        self.assertEqual(1, len(result["errors"]))
        self.assertEqual(1, result["completed"][0]["copied"])
        self.assertEqual([], self.receipts())
        self.targets[1].mkdir()
        result = self.store.run_all("owner", run_id="timer-1")
        self.assertEqual([], result["errors"])
        event, = self.receipts()
        self.assertEqual({"files_copied": 1, "rules_completed": 2}, event.metadata)

    def test_manual_one_rule_has_a_separate_receipt_and_invalid_id_does_nothing(self):
        with self.assertRaises(ValueError):
            self.store.run_all("owner", run_id="../unsafe")
        self.assertEqual([], self.receipts())
        self.store.run_rule(self.rules[0], "owner")
        event, = self.receipts()
        self.assertEqual("replication.rule", event.source)
        self.assertEqual(self.rules[0], event.entity_id)

    def test_completed_event_can_feed_ordered_typed_dry_run_without_delivery(self):
        self.store.run_all("owner", run_id="timer-for-preview")
        event, = self.receipts()
        rules = AutomationStore(self.root)
        rule = rules.create("owner", {
            "name": "Preview typed actions", "enabled": True,
            "trigger": {"type": "event", "name": JOB_EVENT_NAME},
            "conditions": [{"field": "event.metadata.rules_completed", "op": "eq", "value": 2}],
            "actions": [{"type": "test.first"}, {"type": "test.second"}],
        })
        invoked = []
        registry = ActionRegistry()
        for action in ("test.first", "test.second"):
            registry.register(action, lambda *args: invoked.append(args), authorizer=lambda actor, _payload: actor == "owner")
        engine = AutomationEngine(rules, registry)
        result = engine.execute(rule, {
            "name": event.event_type, "id": event.event_id, "metadata": event.metadata,
        }, principal=event.actor, correlation_id=event.correlation_id, dry_run=True)
        self.assertEqual("dry_run", result.status)
        self.assertEqual(["test.first", "test.second"], [item["type"] for item in result.actions])
        self.assertEqual([], invoked)
