"""Persistent, actor-scoped case links (Batch 2).

A link is an explicit, confirmed assignment of domain objects by ID — never
name guessing. Every confirmation and every re-evaluation verifies the
domain rights of the calling actor freshly; a relation never grants rights.
The visit completion date is an explicit user confirmation (``completed_on``),
never derived from ``visit_date`` or ``updated_at``.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..document_store import CONTROL_DIR, atomic_json_write, utc_now
from ..file_lock import exclusive_file_lock
from ..revision_history import RevisionHistory
from .adapters import ContextflowReads, _record_revision, _require_actor
from .contracts import LINK_TYPE, CaseLinkRecord

STORE_FILE = "contextflow-links.json"
BERLIN = ZoneInfo("Europe/Berlin")


_CANONICAL_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _parse_completed_on(value: object) -> date:
    """Parse an explicit ISO completion date; never guess it from records.

    Only the canonical YYYY-MM-DD form is accepted; compact dates and ISO
    week dates are rejected even when ``date.fromisoformat`` would parse them.
    """
    if not isinstance(value, str) or not _CANONICAL_DATE.fullmatch(value.strip()):
        raise ValueError("completed_on must be an ISO date (YYYY-MM-DD)")
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError("completed_on must be an ISO date (YYYY-MM-DD)") from None


class CaseLinkStore:
    """JSON-backed case links with owner separation and assignment dedupe."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        self.path = self.root / CONTROL_DIR / STORE_FILE
        self.lock = self.root / CONTROL_DIR / ".contextflow-links-write.lock"
        self.history = RevisionHistory(self.root)

    def _read(self) -> dict[str, Any]:
        """Read the store file; missing means empty, corrupt means loud.

        A later confirm must never silently overwrite existing data, so only
        a missing file yields an empty store. Unreadable, corrupt or
        wrong-schema content raises instead of being replaced.
        """
        if not self.path.exists():
            return {"version": 1, "links": []}
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise IOError(f"case link store unreadable: {exc}") from exc
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ValueError("case link store is corrupt") from exc
        if not isinstance(data, dict) or not isinstance(data.get("links", []), list):
            raise ValueError("case link store has an unsupported schema")
        data.setdefault("links", [])
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(self.path, data)

    def confirm(
        self,
        actor: str,
        *,
        mail_case_id: str,
        contact_id: str,
        visit_id: str,
        invoice_id: str | None = None,
        completed_on: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Confirm an assignment of existing objects for the actor.

        Verifies every object with the actor's own rights, requires a
        completed visit and an explicit, non-future completion date, and
        dedupes identical assignments. Raises ValueError for bad input and
        propagates PermissionError/ValueError for missing rights or objects.
        """
        actor = _require_actor(actor)
        refs = {
            "mail_case_id": mail_case_id,
            "contact_id": contact_id,
            "visit_id": visit_id,
        }
        for name, ref in refs.items():
            if not isinstance(ref, str) or not ref.strip():
                raise ValueError(f"{name} is required")
        day = _parse_completed_on(completed_on)
        if now is None:
            today = datetime.now(BERLIN).date()
        elif not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("confirm requires an aware datetime as now")
        else:
            today = now.astimezone(BERLIN).date()
        if day > today:
            raise ValueError("completed_on must not be in the future")
        invoice_ref = ""
        if invoice_id is not None:
            invoice_ref = str(invoice_id).strip()
            if not invoice_ref:
                raise ValueError("invoice_id must not be empty")

        reads = ContextflowReads(self.root)
        mail = reads.read_mail_case(actor, mail_case_id)
        contact = reads.read_contact(actor, contact_id)
        visit = reads.read_visit(actor, visit_id)
        if visit["summary"].get("status") != "completed":
            raise ValueError("case link requires a completed visit")
        source_revisions = {
            "mail_case": str(mail["evidence"].get("revision", "")),
            "contact": str(contact["evidence"].get("revision", "")),
            "visit": str(visit["evidence"].get("revision", "")),
        }
        if invoice_ref:
            state = reads.read_invoice_state(
                actor,
                contact_id=contact_id,
                case_link_confirmed=True,
                invoice_id=invoice_ref,
            )
            if state.get("state") != "linked":
                raise ValueError("invoice reference is not confirmed")
            source_revisions["invoice"] = str(
                (state.get("evidence") or {}).get("revision", "")
            )

        revision = _record_revision(
            {
                "assignment": [actor, mail_case_id, contact_id, visit_id],
                "invoice_id": invoice_ref,
                "completed_on": day.isoformat(),
            }
        )
        now_iso = utc_now()
        with exclusive_file_lock(self.lock):
            data = self._read()
            links = data["links"]
            existing = next(
                (
                    row
                    for row in links
                    if isinstance(row, dict)
                    and row.get("owner") == actor
                    and row.get("status", "active") == "active"
                    and str(row.get("mail_case_id", "")) == mail_case_id
                    and str(row.get("contact_id", "")) == contact_id
                    and str(row.get("visit_id", "")) == visit_id
                ),
                None,
            )
            if (
                existing is not None
                and existing.get("completed_on") == day.isoformat()
                and str(existing.get("invoice_id", "")) == invoice_ref
            ):
                return dict(existing)
            if existing is not None:
                existing.update(
                    {
                        "completed_on": day.isoformat(),
                        "invoice_id": invoice_ref,
                        "confirmed_at": now_iso,
                        "confirmed_by": actor,
                        "revision": revision,
                        "status": "active",
                        "source_revisions": source_revisions,
                    }
                )
                row = existing
                action = "contextflow_link_updated"
            else:
                row = {
                    "link_id": str(uuid.uuid4()),
                    "type": LINK_TYPE,
                    "owner": actor,
                    "mail_case_id": mail_case_id,
                    "contact_id": contact_id,
                    "visit_id": visit_id,
                    "invoice_id": invoice_ref,
                    "completed_on": day.isoformat(),
                    "confirmed_at": now_iso,
                    "confirmed_by": actor,
                    "revision": revision,
                    "status": "active",
                    "source_revisions": source_revisions,
                }
                links.append(row)
                action = "contextflow_link_confirmed"
            self._write(data)
        self.history.record(
            action,
            actor,
            "case-link",
            str(row["link_id"]),
            {"completed_on": day.isoformat()},
        )
        return dict(row)

    def _get_owned(self, actor: str, link_id: str) -> dict[str, Any]:
        """Return the actor's own link row without touching domain rights."""
        for row in self._read().get("links", []):
            if (
                isinstance(row, dict)
                and row.get("link_id") == link_id
                and row.get("owner") == actor
                and row.get("status", "active") == "active"
            ):
                return dict(row)
        raise ValueError("case link not found")

    def get(self, actor: str, link_id: str) -> dict[str, Any]:
        """Return the link after verifying fresh domain rights.

        A revoked source right raises before anything is returned, so no
        ids or details leak. Implemented via recheck without recursion.
        """
        return self.recheck(actor, link_id)["link"]

    def list_links(self, actor: str) -> list[dict[str, Any]]:
        """List only links the actor may currently read in full."""
        actor = _require_actor(actor)
        visible: list[dict[str, Any]] = []
        for row in self._read().get("links", []):
            if not (
                isinstance(row, dict)
                and row.get("owner") == actor
                and row.get("status", "active") == "active"
            ):
                continue
            try:
                self.recheck(actor, str(row.get("link_id", "")))
            except (PermissionError, ValueError, KeyError):
                continue
            visible.append(dict(row))
        return visible

    def recheck(self, actor: str, link_id: str) -> dict[str, Any]:
        """Re-read every linked object with fresh rights; raises if revoked.

        Returns the link together with the current adapter outputs. Never
        caches rights or objects across calls.
        """
        actor = _require_actor(actor)
        link = self._get_owned(actor, link_id)
        reads = ContextflowReads(self.root)
        outputs: dict[str, Any] = {
            "mail": reads.read_mail_case(actor, str(link["mail_case_id"])),
            "contact": reads.read_contact(actor, str(link["contact_id"])),
            "visit": reads.read_visit(actor, str(link["visit_id"])),
        }
        invoice_ref = str(link.get("invoice_id", "") or "")
        if invoice_ref:
            outputs["invoice"] = reads.read_invoice_state(
                actor,
                contact_id=str(link["contact_id"]),
                case_link_confirmed=True,
                invoice_id=invoice_ref,
            )
        else:
            outputs["invoice"] = {
                "state": "unknown",
                "reason": "no_confirmed_invoice_reference",
            }
        return {"link": link, "reads": outputs}


def json_loads(path: Path) -> Any:
    """Read a JSON document from disk."""
    return json.loads(path.read_text(encoding="utf-8"))
