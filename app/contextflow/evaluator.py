"""Deterministic 5-day follow-up evaluation (Batch 2).

No model calls and no domain writes. Counts local calendar days in
Europe/Berlin since the explicitly confirmed ``completed_on`` date, requires
a currently completed visit with truly unreviewed findings, and never
invents a missing invoice status. Returns an evidenced hint or None.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .adapters import _require_actor
from .case_links import CaseLinkStore
from .contracts import (
    FOLLOWUP_DAYS,
    FOLLOWUP_SIGNAL,
    FollowupHint,
)

BERLIN = ZoneInfo("Europe/Berlin")


def _fingerprint(parts: list[object]) -> str:
    """Stable hint identifier; changes when any read fact changes."""
    payload = json.dumps([str(part) for part in parts], sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class FollowupEvaluator:
    """Evaluate one confirmed case link for overdue follow-up work."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()

    def evaluate(
        self, actor: str, link_id: str, now: datetime
    ) -> dict[str, Any] | None:
        """Return an evidenced follow-up hint, or None when nothing is due.

        Raises ValueError for empty actors, naive datetimes and invalid or
        future completion dates, and propagates PermissionError/ValueError
        when domain rights were revoked since confirmation.
        """
        actor = _require_actor(actor)
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("evaluate requires an aware datetime")
        checked = CaseLinkStore(self.root).recheck(actor, link_id)
        link = checked["link"]
        reads = checked["reads"]

        try:
            completed = date.fromisoformat(str(link.get("completed_on", "")))
        except ValueError:
            raise ValueError("link has no valid completed_on date") from None
        today = now.astimezone(BERLIN).date()
        if completed > today:
            raise ValueError("completed_on must not be in the future")
        days = (today - completed).days
        if days < FOLLOWUP_DAYS:
            return None

        visit_summary = reads["visit"]["summary"]
        if visit_summary.get("status") != "completed":
            return None
        pending = [
            str(item.get("finding_id", ""))
            for item in visit_summary.get("findings", []) or []
            if isinstance(item, dict)
            and item.get("status") == "unreviewed"
            and item.get("finding_id")
        ]
        if not pending:
            return None

        invoice_state = reads["invoice"]
        evidence = [
            reads["mail"]["evidence"],
            reads["contact"]["evidence"],
            reads["visit"]["evidence"],
        ]
        if invoice_state.get("state") == "linked":
            evidence.append(invoice_state["evidence"])
        hint_id = _fingerprint(
            [
                link["link_id"],
                link["completed_on"],
                [item.get("revision", "") for item in evidence],
                sorted(pending),
                invoice_state.get("state", "unknown"),
                str((invoice_state.get("evidence") or {}).get("revision", "")),
            ]
        )
        hint: FollowupHint = {
            "hint_id": hint_id,
            "link_id": str(link["link_id"]),
            "signal": FOLLOWUP_SIGNAL,
            "reason_code": "unreviewed_findings",
            "certainty": "verified",
            "observed_at": now.isoformat(),
            "completed_on": str(link["completed_on"]),
            "days_since_completion": days,
            "evidence": evidence,
            "pending_finding_ids": pending,
            "proposals": [
                {
                    "type": "task.create",
                    "title": f"Offene Befunde nacharbeiten ({len(pending)})",
                    "approval_required": True,
                    "ref": pending[0],
                }
            ],
            "invoice": invoice_state,
        }
        return dict(hint)
