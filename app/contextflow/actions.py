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
from .case_links import BERLIN, CaseLinkStore
from .evaluator import FollowupEvaluator


class ApprovalStale(ValueError):
    """The hint changed or went away since the user reviewed it."""


def _digest(*parts: object) -> str:
    import json as _json

    payload = _json.dumps([str(part) for part in parts], sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _expected_ids(
    actor: str, link_id: str, expected_hint_id: str, asset_id: str
) -> tuple[str, str]:
    """Stable task resource/UID for one exact approval.

    The expected hint already binds every read fact (sources, revisions,
    pending findings, device context), so the same exact approval always
    resolves to the same slot — even if the evidence evolved afterwards.
    """
    digest = _digest(actor, link_id, expected_hint_id, asset_id)
    return f"cf-{digest[:32]}.ics", f"cf-{digest}@contextflow.local"


def _verified_task(
    todos: TodoStore, resource: str, uid: str, actor: str
) -> dict[str, Any] | None:
    """Return the actor-owned task at the resource only with matching UID.

    The UID is only ever written by this flow for this exact approval, and
    the lookup itself is actor-scoped — a foreign occupant never verifies.
    """
    try:
        existing = todos.get_resource(resource, actor)
    except (PermissionError, ValueError):
        return None
    if isinstance(existing, dict) and str(existing.get("uid", "")) == uid:
        return dict(existing)
    return None


def approve_task(
    actor: str,
    link_id: str,
    expected_hint_id: str,
    approved: bool,
    now: datetime,
    root: str | Path,
    confirmed_asset_id: str = "",
    confirm_device: bool = False,
) -> dict[str, Any]:
    """Create the approved follow-up task exactly once, or return it.

    ``confirmed_asset_id`` is an optional visible prefill: it is only
    honored when ``confirm_device`` is literally True, the asset really
    exists on the linked visit, and at least one pending finding references
    it. Anything else raises ValueError — the device is never guessed.

    Replay safety is bound to the exact approval, not to a fresh
    evaluation: an already-committed task for the same
    (actor, link, expected hint, device) is returned idempotently even if
    the evidence evolved since. Only an approval with no committed task is
    freshness-checked and still rejected as stale when the hint changed.

    Raises ValueError for missing actors, missing explicit approval or bad
    input, ApprovalStale for changed or expired hints without a committed
    task, and propagates PermissionError/ValueError when domain rights were
    revoked.
    """
    actor = _require_actor(actor)
    if approved is not True:
        raise ValueError("explicit approval is required")
    if not isinstance(expected_hint_id, str) or not expected_hint_id.strip():
        raise ValueError("expected hint id is required")
    asset_id = str(confirmed_asset_id or "").strip()
    if asset_id and confirm_device is not True:
        raise ValueError("device prefill requires an explicit confirmation")
    resource, uid = _expected_ids(actor, link_id, expected_hint_id, asset_id)
    todos = TodoStore(Path(root).expanduser())
    committed = _verified_task(todos, resource, uid, actor)
    if committed is not None:
        # Even the owner's own replay revalidates the link and every
        # linked source right first; a deleted link or any revoked
        # mail/contact/visit/invoice access denies the replay instead
        # of serving the committed task. The asset stays bound to this
        # resource identity — nothing is inferred from changed evidence.
        CaseLinkStore(root).get(actor, link_id)
        return {
            "task": committed,
            "created": False,
            "hint_id": expected_hint_id,
            "resource": resource,
            "asset_id": asset_id,
        }
    hint = FollowupEvaluator(root).evaluate(actor, link_id, now)
    if hint is None:
        raise ApprovalStale("hint is no longer current")
    if hint.get("hint_id") != expected_hint_id:
        raise ApprovalStale("hint changed since review")

    pending = [str(item) for item in hint.get("pending_finding_ids", []) or []]
    if not pending:
        raise ApprovalStale("no pending findings left")
    device: dict[str, Any] | None = None
    if asset_id:
        if confirm_device is not True:
            raise ValueError("device prefill requires an explicit confirmation")
        for row in hint.get("device_context", []) or []:
            if isinstance(row, dict) and str(row.get("asset_id", "")) == asset_id:
                device = row
                break
        if device is None:
            raise ValueError("confirmed device is not referenced by pending findings")
    # Migration: tasks created before hint-bound resources existed live
    # under a pending-based digest. Reuse them instead of duplicating.
    legacy_digest = _digest(actor, link_id, sorted(pending), asset_id)
    legacy_resource = f"cf-{legacy_digest[:32]}.ics"
    legacy_uid = f"cf-{legacy_digest}@contextflow.local"
    if legacy_resource != resource:
        legacy = _verified_task(todos, legacy_resource, legacy_uid, actor)
        if legacy is not None:
            CaseLinkStore(root).get(actor, link_id)
            return {
                "task": legacy,
                "created": False,
                "hint_id": expected_hint_id,
                "resource": legacy_resource,
                "asset_id": asset_id,
            }
    contact_refs = [
        str(item.get("id", ""))
        for item in hint.get("evidence", []) or []
        if isinstance(item, dict) and item.get("type") == "contact" and item.get("id")
    ]
    proposal = (hint.get("proposals", []) or [{}])[0]
    title = str(proposal.get("title", "Offene Befunde nacharbeiten"))
    description_lines = [
        f"Belegte Nacharbeit aus ContextFlow-Hinweis {hint['hint_id']}.",
        f"Befunde: {', '.join(sorted(pending))}.",
        "Status: bestätigt, noch nichts erledigt — erst diese Aufgabe abarbeiten.",
        f"Fall-Link: {hint.get('link_id', link_id)}.",
    ]
    if device is not None:
        label = str(device.get("name", "") or asset_id)
        kind = str(device.get("kind", "") or "")
        description_lines.append(
            f"Betroffenes Gerät (von dir bestätigt): {label}"
            + (f" ({kind})" if kind else "")
            + f" — im Ortstermin prüfen: {device.get('url', '')}."
        )
    values: dict[str, Any] = {
        "uid": uid,
        "title": title,
        "description": "\n".join(description_lines)[:4000],
        "contact_id": contact_refs[0] if contact_refs else "",
    }
    try:
        item, created = todos.put_resource(resource, values, actor, create_only=True)
    except TodoConflict as exc:
        existing = exc.item if isinstance(exc.item, dict) else None
        if existing is None:
            existing = todos.get_resource(resource, actor)
        if isinstance(existing, dict) and str(existing.get("uid", "")) == uid:
            return {
                "task": dict(existing),
                "created": False,
                "hint_id": hint["hint_id"],
                "resource": resource,
                "asset_id": asset_id,
            }
        if existing is None:
            raise
        raise ValueError("task resource is occupied by unrelated content")
    return {
        "task": dict(item),
        "created": bool(created),
        "hint_id": hint["hint_id"],
        "resource": resource,
        "asset_id": asset_id,
    }
