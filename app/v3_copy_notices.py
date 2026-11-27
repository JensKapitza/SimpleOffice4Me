"""Local, account-scoped notices from confirmed browser document copies."""
from __future__ import annotations

import hashlib
import logging
import sqlite3

from .file_lock import exclusive_file_lock
from .v3_activity import ActivityStore, DomainEvent
from .v3_automation import ActionRegistry, AutomationEngine, AutomationStore
from .v3_capabilities import enabled


COPY_EVENT = "document.copy_completed"
ACTION = "app.copy_notice"
NOTICE = "automation.notice.copy_completed"
PRESET = {"copy_notice_preset": True}
LOG = logging.getLogger(__name__)


def dispatch_copy_notice(root, event: DomainEvent) -> None:
    """Run only explicit local notice rules, never unrelated actions or targets."""
    if (event.event_type != COPY_EVENT or event.source != "browser.document_copy"
            or not event.actor or not event.event_id):
        return
    try:
        if not enabled("v3.automation", root=root):
            return
        store = AutomationStore(root)
        actions = ActionRegistry()

        def record(principal, _payload, _context):
            ActivityStore(root).record(
                NOTICE, principal, event_id=hashlib.sha256(f"{NOTICE}\0{principal}\0{event.event_id}".encode()).hexdigest(),
                correlation_id=event.event_id, source=ACTION,
            )

        actions.register(ACTION, record, authorizer=lambda principal, payload: principal == event.actor and not payload)
        engine = AutomationEngine(store, actions)
        for candidate in store.list(event.actor, enabled_only=True):
            if not is_local_copy_notice_rule(candidate):
                continue
            with store.rule_lock(candidate.rule_id):
                rule = store.get(candidate.rule_id, event.actor)
                if not rule.enabled or not is_local_copy_notice_rule(rule):
                    continue
                engine.execute(
                    rule, {"name": COPY_EVENT, "id": event.event_id, "occurred_at": event.occurred_at},
                    principal=event.actor, correlation_id=event.event_id,
                )
    except (OSError, sqlite3.Error, ValueError) as exc:
        LOG.warning("local copy notice could not be processed: %s", type(exc).__name__)


def is_local_copy_notice_rule(rule) -> bool:
    if rule.trigger.get("type") != "event" or rule.trigger.get("name") != COPY_EVENT or len(rule.actions) != 1:
        return False
    action = rule.actions[0]
    timing = action.get("timing", {})
    # This source is immediate. Delayed execution requires a durable scheduler.
    return (
        action.get("type") == ACTION
        and action.get("payload", {}) == {}
        and set(action) <= {"type", "payload", "timing"}
        and not timing.get("delay_seconds", 0)
        and "slot_start" not in timing
    )


def enable_copy_notice_preset(store: AutomationStore, principal: str, name: str) -> None:
    """A repeated enable activates the existing preset, rather than adding a rule."""
    lock_id = hashlib.sha256(principal.encode()).hexdigest()
    with exclusive_file_lock(store.path.parent / f".copy-notice-preset-{lock_id}.lock"):
        for rule in store.list(principal):
            if rule.scope == PRESET and is_local_copy_notice_rule(rule):
                if not rule.enabled:
                    store.update(rule.rule_id, principal, {"enabled": True})
                return
        store.create(principal, {
            "name": name, "enabled": True, "scope": PRESET,
            "trigger": {"type": "event", "name": COPY_EVENT},
            "actions": [{"type": ACTION, "payload": {}}],
        })


def recent_copy_notices(root, principal: str):
    if not principal or not enabled("v3.activity", root=root):
        return []
    try:
        return ActivityStore(root).list(actor=principal, event_type=NOTICE, limit=10)
    except (OSError, sqlite3.Error) as exc:
        LOG.warning("local copy notices could not be loaded: %s", type(exc).__name__)
        return []
