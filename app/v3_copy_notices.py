"""Local, account-scoped notices from confirmed browser copies and replication batches."""
from __future__ import annotations

import hashlib
import logging
import sqlite3

from .document_copy_events import EVENT_NAME as COPY_EVENT, JOB_EVENT_NAME
from .file_lock import exclusive_file_lock
from .v3_activity import ActivityStore, DomainEvent
from .v3_automation import ActionRegistry, AutomationEngine, AutomationStore
from .v3_capabilities import enabled


ACTION = "app.copy_notice"
NOTICE = "automation.notice.copy_completed"
JOB_NOTICE = "automation.notice.copy_job_completed"
NOTICE_EVENTS = {COPY_EVENT: NOTICE, JOB_EVENT_NAME: JOB_NOTICE}
PRESET = {"copy_notice_preset": True}
LOG = logging.getLogger(__name__)


def dispatch_copy_notice(root, event: DomainEvent) -> None:
    if event.event_type == COPY_EVENT and event.source == "browser.document_copy":
        _dispatch(root, event, event.actor)


def dispatch_replication_notice(root, event_id: str, actor: str, recipient: str) -> None:
    """Used only by the local CLI after authorizing the named account."""
    try:
        event = ActivityStore(root).find_for_actor(event_id, actor)
        if event and event.event_type == JOB_EVENT_NAME and event.source == "replication.batch":
            _dispatch(root, event, recipient)
    except (OSError, sqlite3.Error, ValueError) as exc:
        LOG.warning("replication notice could not be processed: %s", type(exc).__name__)


def _dispatch(root, event: DomainEvent, recipient: str) -> None:
    """Run only explicit local notice rules, never unrelated actions or targets."""
    if not recipient or not event.event_id:
        return
    event_type = event.event_type
    notice_type = NOTICE_EVENTS[event_type]
    try:
        if not enabled("v3.automation", root=root):
            return
        store = AutomationStore(root)
        actions = ActionRegistry()

        def record(principal, _payload, _context):
            ActivityStore(root).record(
                notice_type, principal, event_id=hashlib.sha256(
                    f"{notice_type}\0{principal}\0{event.event_id}".encode(),
                ).hexdigest(),
                correlation_id=event.event_id, source=ACTION,
            )

        actions.register(ACTION, record, authorizer=lambda principal, payload: principal == recipient and not payload)
        engine = AutomationEngine(store, actions)
        for candidate in store.list(recipient, enabled_only=True):
            if not is_local_copy_notice_rule(candidate, event_type):
                continue
            with store.rule_lock(candidate.rule_id):
                rule = store.get(candidate.rule_id, recipient)
                if not rule.enabled or not is_local_copy_notice_rule(rule, event_type):
                    continue
                engine.execute(
                    rule, {"name": event_type, "id": event.event_id, "occurred_at": event.occurred_at},
                    principal=recipient, correlation_id=event.event_id,
                )
    except (OSError, sqlite3.Error, ValueError) as exc:
        LOG.warning("local copy notice could not be processed: %s", type(exc).__name__)


def is_local_copy_notice_rule(rule, event_type: str = COPY_EVENT) -> bool:
    if (event_type not in NOTICE_EVENTS or rule.trigger.get("type") != "event"
            or rule.trigger.get("name") != event_type or len(rule.actions) != 1):
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


def enable_copy_notice_preset(
    store: AutomationStore, principal: str, name: str, event_type: str = COPY_EVENT,
) -> None:
    """A repeated enable activates the existing preset, rather than adding a rule."""
    if event_type not in NOTICE_EVENTS:
        raise ValueError("unsupported copy notice trigger")
    lock_id = hashlib.sha256(principal.encode()).hexdigest()
    with exclusive_file_lock(store.path.parent / f".copy-notice-preset-{lock_id}.lock"):
        for rule in store.list(principal):
            if rule.scope == PRESET and is_local_copy_notice_rule(rule, event_type):
                if not rule.enabled:
                    store.update(rule.rule_id, principal, {"enabled": True})
                return
        store.create(principal, {
            "name": name, "enabled": True, "scope": PRESET,
            "trigger": {"type": "event", "name": event_type},
            "actions": [{"type": ACTION, "payload": {}}],
        })


def recent_copy_notices(root, principal: str):
    if not principal or not enabled("v3.activity", root=root):
        return []
    try:
        store = ActivityStore(root)
        rows = [
            notice for kind in NOTICE_EVENTS.values()
            for notice in store.list(actor=principal, event_type=kind, limit=10)
        ]
        return sorted(rows, key=lambda row: (row.occurred_at, row.event_id), reverse=True)[:10]
    except (OSError, sqlite3.Error) as exc:
        LOG.warning("local copy notices could not be loaded: %s", type(exc).__name__)
        return []
