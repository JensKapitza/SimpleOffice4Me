"""Optional, durable activity receipts for completed file copies."""
from __future__ import annotations

import logging
import re
import sqlite3
import uuid
from pathlib import Path

from .v3_activity import ActivityStore
from .v3_capabilities import enabled


EVENT_NAME = "document.copy_completed"
JOB_EVENT_NAME = "document.copy_job_completed"
_LOG = logging.getLogger(__name__)


def copy_run_id(value: str) -> str:
    """Pass the same opaque run ID for retries and a new one for each schedule."""
    if not value:
        return uuid.uuid4().hex
    if re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", value) is None:
        raise ValueError("Invalid replication run ID")
    return value


def _capture(root: str | Path, actor: str, name: str, kind: str, entity_id: str,
             operation_id: str, source: str, metadata: dict | None = None) -> str | None:
    try:
        if not enabled("v3.activity", root=root):
            return None
        if not entity_id or not actor:
            raise ValueError("a verified copy identity and actor are required")
        event_id = uuid.uuid5(uuid.NAMESPACE_URL, f"simpleoffice:{name}:{source}:{operation_id}").hex
        event = ActivityStore(root).record(
            name, actor, entity_type=kind, entity_id=entity_id, event_id=event_id,
            correlation_id=event_id, source=source, metadata=metadata,
        )
        return event.event_id
    except (OSError, sqlite3.Error, ValueError) as exc:
        # An optional receipt must not change an already verified operation result.
        _LOG.warning("verified copy activity could not be stored: %s", type(exc).__name__)
        return None


def capture_copy_completed(root: str | Path, actor: str, copy_id: str) -> str | None:
    """Call only after confirmation by the storage boundary; never retry the copy here."""
    return _capture(root, actor, EVENT_NAME, "document", copy_id, copy_id, "browser.document_copy")


def capture_copy_job_completed(root: str | Path, actor: str, run_id: str, *,
                               files_copied: int, rules_completed: int,
                               rule_id: str = "") -> str | None:
    """Capture one receipt after all intended copies and job accounting succeeded."""
    if files_copied <= 0 or rules_completed <= 0:
        return None
    source = "replication.rule" if rule_id else "replication.batch"
    identity = "\0".join((actor, rule_id, run_id))
    return _capture(
        root, actor, JOB_EVENT_NAME, "replication_rule" if rule_id else "replication_run",
        rule_id or run_id, identity, source,
        {"files_copied": files_copied, "rules_completed": rules_completed},
    )
