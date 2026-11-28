"""Local, account-scoped notices from confirmed browser copies and replication batches."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
import math
from pathlib import Path
import sqlite3

from .document_copy_events import EVENT_NAME as COPY_EVENT, JOB_EVENT_NAME
from flask import current_app, has_app_context

from .db import get_db
from .file_lock import exclusive_file_lock
from .v3_activity import ActivityStore, DomainEvent
from .v3_automation import ActionRegistry, AutomationEngine, AutomationStore, ExecutionResult
from .v3_capabilities import enabled
from .v3_automation_timing import received_window_status, timing_envelope, utc_instant
from .v3_jobs import JobStore, PermanentJobError, RetryableJobError
from .workflow_peer_notices import PEER_NOTICE
from .workflow_peer_notice_jobs import dispatch as dispatch_peer_notice


ACTION = "app.copy_notice"
NOTICE_JOB_KIND = "automation.copy_notice"
TIMER_PRESETS = (0, 30, 300)
NOTICE = "automation.notice.copy_completed"
JOB_NOTICE = "automation.notice.copy_job_completed"
NOTICE_EVENTS = {COPY_EVENT: NOTICE, JOB_EVENT_NAME: JOB_NOTICE}
PRESET = {"copy_notice_preset": True}
LOG = logging.getLogger(__name__)


def dispatch_copy_notice(root, event: DomainEvent) -> None:
    if event.event_type == COPY_EVENT and event.source == "browser.document_copy":
        _dispatch(root, event, event.actor)
        dispatch_peer_notice(root, event, event.actor)


def dispatch_replication_notice(root, event_id: str, actor: str, recipient: str) -> None:
    """Used only by the local CLI after authorizing the named account."""
    try:
        event = ActivityStore(root).find_for_actor(event_id, actor)
        if event and event.event_type == JOB_EVENT_NAME and event.source == "replication.batch":
            _dispatch(root, event, recipient)
            dispatch_peer_notice(root, event, recipient)
    except (OSError, sqlite3.Error, ValueError) as exc:
        LOG.warning("replication notice could not be processed: %s", type(exc).__name__)


def _now():
    return datetime.now(timezone.utc)


def _account(root, principal):
    if not has_app_context() or Path(current_app.config["DOCUMENT_ROOT"]).resolve() != Path(root).resolve():
        return None
    account = get_db().execute(
        """SELECT id,auth_version FROM user
           WHERE username=? COLLATE BINARY AND COALESCE(is_disabled, 0)=0""",
        (principal,),
    ).fetchone()
    return list(account) if account else None


def _engine(root, store, event, recipient, *, still_authorized=lambda: True):
    notice_type = NOTICE_EVENTS[event.event_type]
    actions = ActionRegistry()

    def record(principal, _payload, _context):
        ActivityStore(root).record(
            notice_type, principal, event_id=hashlib.sha256(
                f"{notice_type}\0{principal}\0{event.event_id}".encode(),
            ).hexdigest(),
            correlation_id=event.event_id, source=ACTION,
        )

    actions.register(
        ACTION, record,
        authorizer=lambda principal, payload: principal == recipient and not payload and still_authorized(),
    )
    return AutomationEngine(store, actions, clock=_now)


def _execute(engine, rule, event, recipient):
    return engine.execute(
        rule, {"name": event.event_type, "id": event.event_id, "occurred_at": event.occurred_at},
        principal=recipient, correlation_id=event.event_id,
    )


def _schedule(root, store, rule, event, recipient, result):
    account = _account(root, recipient)
    key = hashlib.sha256(f"{NOTICE_JOB_KIND}\0{rule.rule_id}\0{rule.version}\0{event.event_id}".encode()).hexdigest()
    queued = False
    if account and enabled("v3.jobs", root=root):
        window = {k: v for k, v in result.actions[0].items() if k not in {"state", "type"}}
        if window.get("expires_at"):
            try:
                JobStore(root).enqueue(
                    NOTICE_JOB_KIND,
                    {"event_id": event.event_id, "actor": event.actor, "rule_id": rule.rule_id,
                     "version": rule.version, "account": account, "window": window},
                    recipient, idempotency_key=key, not_before=math.ceil(utc_instant(window["not_before"]).timestamp()),
                    max_open_per_principal=100, max_open_total=1000,
                )
                queued = True
            except (sqlite3.Error, OSError, ValueError) as exc:
                LOG.warning("copy notice could not be queued: %s", type(exc).__name__)
    status = result if queued else ExecutionResult("schedule_unavailable", True, False, ())
    store.record_execution(rule, key="queue:" + key, correlation_id=event.event_id, principal=recipient, result=status)


def _dispatch(root, event: DomainEvent, recipient: str) -> None:
    """Run only explicit local notice rules, never unrelated actions or targets."""
    if not recipient or not event.event_id or event.event_type not in NOTICE_EVENTS:
        return
    try:
        if not enabled("v3.automation", root=root):
            return
        store = AutomationStore(root)
        engine = _engine(root, store, event, recipient)
        for candidate in store.list(recipient, enabled_only=True):
            if not is_local_copy_notice_rule(candidate, event.event_type):
                continue
            with store.rule_lock(candidate.rule_id):
                rule = store.get(candidate.rule_id, recipient)
                if not rule.enabled or not is_local_copy_notice_rule(rule, event.event_type):
                    continue
                result = _execute(engine, rule, event, recipient)
                if result.status == "waiting_for_window":
                    _schedule(root, store, rule, event, recipient, result)
    except (OSError, sqlite3.Error, ValueError) as exc:
        LOG.warning("local copy notice could not be processed: %s", type(exc).__name__)


def copy_notice_job_handler(root):
    """Reauthorize and resolve one durable local notice; never dispatch peers."""
    def run(job):
        from . import app

        with app.app_context():
            _deliver_notice(root, job)

    return run


def _deliver_notice(root, job):
    if not all(enabled(key, root=root) for key in ("v3.jobs", "v3.activity", "v3.automation")):
        raise PermanentJobError("copy notice is disabled")
    data = job.payload
    receiver = job.principal
    if not data.get("account") or _account(root, receiver) != data["account"]:
        raise PermanentJobError("copy notice account is no longer authorized")
    event = ActivityStore(root).find_for_actor(data["event_id"], data["actor"])
    sources = {COPY_EVENT: "browser.document_copy", JOB_EVENT_NAME: "replication.batch"}
    if (not event or sources.get(event.event_type) != event.source
            or (event.event_type == COPY_EVENT and receiver != event.actor)):
        raise PermanentJobError("copy notice event is no longer available")
    store = AutomationStore(root)
    with store.rule_lock(data["rule_id"]):
        try:
            rule = store.get(data["rule_id"], receiver)
        except LookupError:
            raise PermanentJobError("copy notice rule is no longer available") from None
        if not rule.enabled or rule.version != data["version"] or not is_local_copy_notice_rule(rule, event.event_type):
            raise PermanentJobError("copy notice rule has changed")
        actual = timing_envelope(rule.actions[0]["timing"], event.occurred_at, rule.scope.get("timezone", "UTC"))
        if actual != data["window"]:
            raise PermanentJobError("copy notice time window changed")
        if received_window_status(data["window"], _now()) == "waiting_for_window":
            raise RetryableJobError("copy notice is not due yet")
        engine = _engine(root, store, event, receiver, still_authorized=lambda: _account(root, receiver) == data["account"])
        result = _execute(engine, rule, event, receiver)
        if result.status == "waiting_for_window":
            raise RetryableJobError("copy notice is not due yet")
        if result.status not in {"succeeded", "window_missed", "duplicate"}:
            raise PermanentJobError("copy notice could not be recorded")


def is_local_copy_notice_rule(rule, event_type: str = COPY_EVENT) -> bool:
    if (event_type not in NOTICE_EVENTS or rule.trigger.get("type") != "event"
            or rule.trigger.get("name") != event_type or len(rule.actions) != 1):
        return False
    action = rule.actions[0]
    timing = action.get("timing", {})
    return (
        action.get("type") == ACTION
        and action.get("payload", {}) == {}
        and set(action) <= {"type", "payload", "timing"}
        and "slot_start" not in timing
        and (not timing.get("delay_seconds", 0) or "within_seconds" in timing)
    )


def enable_copy_notice_preset(
    store: AutomationStore, principal: str, name: str, event_type: str = COPY_EVENT, *, delay: int = 0,
) -> None:
    """A repeated enable activates the existing preset, rather than adding a rule."""
    if event_type not in NOTICE_EVENTS:
        raise ValueError("unsupported copy notice trigger")
    if delay not in TIMER_PRESETS or type(delay) is not int:
        raise ValueError("unsupported notice delay")
    action = {"type": ACTION, "payload": {}}
    if delay:
        action["timing"] = {"delay_seconds": delay, "within_seconds": delay + 300}
    lock_id = hashlib.sha256(principal.encode()).hexdigest()
    with exclusive_file_lock(store.path.parent / f".copy-notice-preset-{lock_id}.lock"):
        for rule in store.list(principal):
            if rule.scope == PRESET and is_local_copy_notice_rule(rule, event_type):
                if not rule.enabled:
                    store.update(rule.rule_id, principal, {"enabled": True, "actions": [action]})
                return
        store.create(principal, {
            "name": name, "enabled": True, "scope": PRESET,
            "trigger": {"type": "event", "name": event_type},
            "actions": [action],
        })


def recent_copy_notices(root, principal: str, *, strict=False):
    if not principal or not enabled("v3.activity", root=root):
        return []
    try:
        store = ActivityStore(root)
        rows = [
            notice for kind in (*NOTICE_EVENTS.values(), PEER_NOTICE)
            for notice in store.list(actor=principal, event_type=kind, limit=10)
        ]
        return sorted(rows, key=lambda row: (row.occurred_at, row.event_id), reverse=True)[:10]
    except (OSError, sqlite3.Error) as exc:
        LOG.warning("local copy notices could not be loaded: %s", type(exc).__name__)
        if strict:
            raise
        return []
