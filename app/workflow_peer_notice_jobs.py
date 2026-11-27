"""Queue a fixed, authorized account notice for asynchronous delivery to a peer."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
import math
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app, has_app_context

from .access_control import has_feature
from .db import get_db
from .document_copy_events import EVENT_NAME, JOB_EVENT_NAME
from .file_lock import exclusive_file_lock
from .v3_activity import ActivityStore
from .v3_automation import AutomationStore, ExecutionResult
from .v3_automation_timing import received_window_status, timing_envelope, utc_instant
from .v3_capabilities import enabled
from .v3_jobs import JobStore, PermanentJobError, RetryableJobError
from .workflow_peer_notices import DEST, SOURCES, _now, feature_on, permitted_peer, send_notice


KIND = "automation.peer_copy_notice"
ACTION = "app.peer_copy_notice"
DELAYS = (0, 30)
DEADLINES = (10, 120, 600)
DAY_END = "end_of_day"
PRESET = {"peer_copy_notice_preset": True}
LOG = logging.getLogger(__name__)


def available(root):
    return feature_on(root) and enabled("v3.jobs", root=root)


def _account(root, name):
    if not has_app_context() or Path(current_app.config["DOCUMENT_ROOT"]).resolve() != Path(root).resolve():
        return None
    account = get_db().execute(
        "SELECT id, username, is_admin, is_disabled, auth_version FROM user WHERE username=? COLLATE BINARY",
        (name,),
    ).fetchone()
    if account is None or not has_feature(account, "sync"):
        return None
    return [account["id"], account["auth_version"]]


def _timing(delay, deadline, zone):
    if type(delay) is not int or delay not in DELAYS:
        raise ValueError("invalid delay for peer notice")
    if deadline == DAY_END:
        return {"delay_seconds": delay, "end_of_day": True, "timezone": zone}
    if type(deadline) is not int or deadline not in DEADLINES or delay >= deadline:
        raise ValueError("invalid deadline for peer notice")
    return {"delay_seconds": delay, "within_seconds": deadline, "timezone": zone}


def _action(rule, event_type):
    if (rule.scope != PRESET or rule.trigger != {"type": "event", "name": event_type}
            or rule.conditions or len(rule.actions) != 1):
        return None
    action = rule.actions[0]
    payload = action.get("payload")
    timing = action.get("timing")
    if (set(action) != {"type", "payload", "timing"} or action["type"] != ACTION
            or not isinstance(payload, dict) or set(payload) != {"peer", "destination"}
            or not isinstance(payload["peer"], str) or not isinstance(payload["destination"], str)
            or not DEST.fullmatch(payload["destination"]) or not isinstance(timing, dict)):
        return None
    try:
        deadline = DAY_END if timing.get("end_of_day") is True else timing.get("within_seconds")
        return action if timing == _timing(timing.get("delay_seconds"), deadline, timing.get("timezone")) else None
    except ValueError:
        return None


def save_rule(root, owner, peer, destination, event_type, *, delay, deadline, zone, name):
    if not available(root) or not _account(root, owner):
        raise PermissionError("workflow notice account is not authorized")
    peer = permitted_peer(root, peer, "send")["peer_id"]
    if not DEST.fullmatch(destination) or event_type not in (EVENT_NAME, JOB_EVENT_NAME):
        raise ValueError("invalid peer copy notice rule")
    store = AutomationStore(root)
    action = {"type": ACTION, "payload": {"peer": peer, "destination": destination},
              "timing": _timing(delay, deadline, zone)}
    lock_id = hashlib.sha256(owner.encode()).hexdigest()
    with exclusive_file_lock(store.path.parent / f".peer-copy-rule-{lock_id}.lock"):
        for rule in store.list(owner):
            previous = _action(rule, event_type)
            if previous and previous["payload"] == action["payload"]:
                if not rule.enabled or previous != action:
                    return store.update(rule.rule_id, owner, {"enabled": True, "actions": [action]})
                return rule
        return store.create(owner, {"name": name, "enabled": True, "scope": PRESET,
                                    "trigger": {"type": "event", "name": event_type}, "actions": [action]})


def _record(store, rule, event, principal, key, state, window):
    store.record_execution(
        rule, key=f"{state}:{key}", correlation_id=event.event_id, principal=principal,
        result=ExecutionResult(state, True, False, ({"type": ACTION, "state": state, **window},)),
    )


def dispatch(root, event, principal):
    """No network I/O in the copy path; only persist a bounded typed intent."""
    if not principal or SOURCES.get(event.event_type) != event.source:
        return
    if event.event_type == EVENT_NAME and event.actor != principal:
        return
    try:
        if not available(root) or not (account := _account(root, principal)):
            return
        store = AutomationStore(root)
        for rule in store.list(principal, enabled_only=True):
            action = _action(rule, event.event_type)
            if not action:
                continue
            window = timing_envelope(action["timing"], event.occurred_at)
            key = hashlib.sha256(f"{KIND}\0{rule.rule_id}\0{rule.version}\0{event.event_id}".encode()).hexdigest()
            state = received_window_status(window, _now())
            if state != "window_missed":
                try:
                    JobStore(root).enqueue(
                        KIND, {"rule_id": rule.rule_id, "version": rule.version,
                               "event_id": event.event_id, "actor": event.actor, "account": account,
                               "window": window}, principal, idempotency_key=key,
                        max_attempts=40 if action["timing"].get("end_of_day") else 10,
                        not_before=math.ceil(utc_instant(window["not_before"]).timestamp()),
                        max_open_per_principal=50, max_open_total=500,
                        max_created_per_principal_window=300, window_seconds=3600,
                    )
                except (OSError, sqlite3.Error, ValueError) as exc:
                    LOG.warning("peer copy notice could not be queued: %s", type(exc).__name__)
                    state = "peer_queue_unavailable"
                else:
                    state = "peer_queued"
            _record(store, rule, event, principal, key, state, window)
    except (OSError, sqlite3.Error, ValueError) as exc:
        LOG.warning("peer copy notice dispatch did not complete: %s", type(exc).__name__)


def _deliver(root, job):
    data, principal = job.payload, job.principal
    if not available(root) or not data.get("account") or _account(root, principal) != data["account"]:
        raise PermanentJobError("peer copy notice account or capability is no longer authorized")
    store = AutomationStore(root)
    event = ActivityStore(root).find_for_actor(data["event_id"], data["actor"])
    if (not event or SOURCES.get(event.event_type) != event.source
            or (event.event_type == EVENT_NAME and event.actor != principal)):
        raise PermanentJobError("copy event no longer available")
    with store.rule_lock(data["rule_id"]):
        try:
            rule = store.get(data["rule_id"], principal)
        except LookupError:
            raise PermanentJobError("peer copy notice rule no longer available") from None
        action = _action(rule, event.event_type)
        if not rule.enabled or rule.version != data["version"] or not action:
            raise PermanentJobError("peer copy notice rule has changed")
        window = timing_envelope(action["timing"], event.occurred_at)
        if window != data["window"]:
            raise PermanentJobError("peer copy notice time window has changed")
        if not available(root) or _account(root, principal) != data["account"]:
            raise PermanentJobError("peer copy notice account or capability is no longer authorized")
        status = received_window_status(window, _now())
        if status == "ready":
            try:
                status = send_notice(root, action["payload"]["peer"], action["payload"]["destination"], event,
                                     window=window)
            except ConnectionError:
                status = "peer_unreachable"
            except PermissionError:
                status = "denied"
            except ValueError:
                status = "peer_invalid"
        _record(store, rule, event, principal, job.idempotency_key, status, window)
        if status in {"peer_unreachable", "waiting_for_window", "recipient_busy"}:
            raise RetryableJobError(status)
        if status not in {"delivered", "duplicate", "window_missed"}:
            raise PermanentJobError(status)


def job_handler(root):
    def run(job):
        from . import app

        with app.app_context():
            _deliver(root, job)

    return run


def _receipt_state(job, known, window, now, *, worker_enabled):
    if known in {"delivered", "duplicate"}:
        return "delivered"
    if known == "window_missed":
        return "window_missed"
    if job.state == "cancelled":
        return "cancelled"
    if job.state == "failed":
        return "denied" if job.last_error == "denied" else "failed"
    if job.state == "succeeded":
        return "unknown"
    window_state = received_window_status(window, now)
    if window_state == "window_missed":
        return window_state
    if not worker_enabled:
        return "queue_paused"
    if window_state != "ready":
        return window_state
    if job.state == "retry_scheduled":
        return "peer_unreachable" if job.last_error == "peer_unreachable" else "retry_scheduled"
    return "sending" if job.state == "running" else "peer_queued"


def receipts(root, principal, store, rules, *, clock=_now):
    """A private bounded read model: one current outcome per job, grouped by original copy."""
    account = _account(root, principal)
    if not feature_on(root) or not account:
        return []
    jobs = [job for job in JobStore(root).recent_for_principal(principal, KIND)
            if job.payload.get("account") == account]
    known = store.recent_peer_outcomes(principal, [job.idempotency_key for job in jobs])
    labels = {(rule.rule_id, rule.version): rule.name for rule in rules if rule.scope == PRESET}
    groups = {}
    now = clock()
    worker_enabled = enabled("v3.jobs", root=root)
    for job in jobs:
        try:
            window = job.payload["window"]
            end = utc_instant(window["expires_at"])
            zone = ZoneInfo(window["origin_timezone"])
            state = _receipt_state(job, known.get(job.idempotency_key), window, now, worker_enabled=worker_enabled)
            if state in {"peer_unreachable", "retry_scheduled"}:
                next_at = datetime.fromtimestamp(job.available_at, timezone.utc)
                if next_at >= end:
                    state = "retry_after_deadline"
            group = groups.setdefault(job.payload["event_id"], {"at": window["requested_at"], "targets": []})
            group["targets"].append({
                "name": labels.get((job.payload["rule_id"], job.payload["version"]), ""),
                "state": state,
                "deadline": end.isoformat(),
                "local": end.astimezone(zone).strftime("%Y-%m-%d %H:%M:%S %Z"),
                "zone": zone.key,
                "attempt": job.attempt,
            })
        except (ValueError, TypeError, KeyError, OverflowError, ZoneInfoNotFoundError) as exc:
            LOG.warning("peer copy receipt could not be rendered: %s", type(exc).__name__)
    return list(groups.values())
