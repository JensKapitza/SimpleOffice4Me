"""Approved, idempotent task creation for ContextFlow (Batch 3).

Only one fixed, audited write exists here: a follow-up task derived from a
verified hint. The caller must pass an explicit literal approval together
with the exact expected hint id; stale hints are rejected. Repeating the
same approval (retry, restart, concurrency) returns the existing task
instead of creating a duplicate, and never overwrites it.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from ..todo_store import TodoConflict, TodoStore
from .adapters import _require_actor
from .case_links import BERLIN
from .evaluator import FollowupEvaluator


class ApprovalStale(ValueError):
    """The hint changed or went away since the user reviewed it."""


def _digest(*parts: object) -> str:
    import json as _json

    payload = _json.dumps([str(part) for part in parts], sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def approve_task(
    actor: str,
    link_id: str,
    expected_hint_id: str,
    approved: bool,
    now: datetime,
    root: str | Path,
) -> dict[str, Any]:
    """Create the approved follow-up task exactly once, or return it.

    Raises ValueError for missing actors, missing explicit approval or bad
    input, ApprovalStale for changed or expired hints, and propagates
    PermissionError/ValueError when domain rights were revoked.
    """
    actor = _require_actor(actor)
    if approved is not True:
        raise ValueError("explicit approval is required")
    if not isinstance(expected_hint_id, str) or not expected_hint_id.strip():
        raise ValueError("expected hint id is required")
    hint = FollowupEvaluator(root).evaluate(actor, link_id, now)
    if hint is None:
        raise ApprovalStale("hint is no longer current")
    if hint.get("hint_id") != expected_hint_id:
        raise ApprovalStale("hint changed since review")

    pending = [str(item) for item in hint.get("pending_finding_ids", []) or []]
    if not pending:
        raise ApprovalStale("no pending findings left")
    digest = _digest(actor, link_id, sorted(pending))
    resource = f"cf-{digest[:32]}.ics"
    uid = f"cf-{digest}@contextflow.local"
    contact_refs = [
        str(item.get("id", ""))
        for item in hint.get("evidence", []) or []
        if isinstance(item, dict) and item.get("type") == "contact" and item.get("id")
    ]
    proposal = (hint.get("proposals", []) or [{}])[0]
    values: dict[str, Any] = {
        "uid": uid,
        "title": str(proposal.get("title", "Offene Befunde nacharbeiten")),
        "contact_id": contact_refs[0] if contact_refs else "",
    }
    todos = TodoStore(Path(root).expanduser())
    try:
        item, created = todos.put_resource(resource, values, actor, create_only=True)
    except TodoConflict as exc:
        existing = exc.item if isinstance(exc.item, dict) else None
        if existing is None:
            existing = todos.get_resource(resource, actor)
        if existing is None:
            raise
        return {
            "task": dict(existing),
            "created": False,
            "hint_id": hint["hint_id"],
            "resource": resource,
        }
    return {
        "task": dict(item),
        "created": bool(created),
        "hint_id": hint["hint_id"],
        "resource": resource,
    }
