"""ContextFlow web UI (Batch 3).

The blueprint is registered unconditionally but every route answers 404
unless ContextFlow is explicitly enabled. All reads stay actor-scoped and
authorized; writes are limited to explicit confirmations. No invoice
finalization, no SMTP, no model calls in this module.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from flask import (
    Blueprint,
    abort,
    current_app,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from ..access_control import has_feature
from ..auth import login_required
from ..contact_store import ContactStore
from ..mail_case_store import MailCaseStore
from ..site_visit_store import SiteVisitStore
from .actions import ApprovalStale, approve_task
from .case_links import BERLIN, CaseLinkStore
from .contracts import MAIL_CASE_URL, TASKS_URL, VISIT_URL
from .evaluator import FollowupEvaluator
from .hints import HintStates

bp = Blueprint("contextflow", __name__, url_prefix="/contextflow")
INVOICE_EDITOR_URL = "/documents/business/contacts/{contact_id}/invoice"


def _enabled() -> bool:
    """ContextFlow is off unless explicitly enabled via config or env."""
    return bool(
        current_app.config.get("CONTEXTFLOW_ENABLED", False)
        or os.environ.get("CONTEXTFLOW_ENABLED") == "1"
    )


def _gate() -> None:
    if not _enabled():
        abort(404)


def _actor() -> str:
    user = g.get("user") or {}
    name = str(user.get("username", "") or "")
    if not name:
        abort(403)
    if not has_feature(user, "documents"):
        abort(403)
    return name


def _root() -> Path:
    return Path(current_app.config["DOCUMENT_ROOT"])


def _now() -> datetime:
    return datetime.now(BERLIN)


def _mail_url(case_id: str) -> str:
    return MAIL_CASE_URL.format(case_id=quote(case_id, safe=""))


def _visit_url(visit_id: str) -> str:
    return VISIT_URL.format(visit_id=quote(visit_id, safe=""))


def _evidence_display(evidence: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Pre-rendered evidence rows for the template (no logic in Jinja)."""
    rows = []
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "type": str(item.get("type", "")),
                "id": str(item.get("id", "")),
                "short": str(item.get("id", ""))[:8],
                "revision": str(item.get("revision", ""))[:8],
                "url": str(item.get("url", "") or ""),
            }
        )
    return rows


def _card(
    root: Path, actor: str, link: dict[str, Any], hint: dict[str, Any] | None
) -> dict[str, Any]:
    """Assemble one dashboard card with safe links and editor deep link."""
    contact_id = str(link.get("contact_id", ""))
    can_manage = False
    try:
        can_manage = bool(ContactStore(root).can_manage(contact_id, actor))
    except (PermissionError, ValueError, KeyError):
        can_manage = False
    editor_url = (
        INVOICE_EDITOR_URL.format(contact_id=quote(contact_id, safe=""))
        if can_manage and contact_id
        else ""
    )
    evidence = _evidence_display((hint or {}).get("evidence", []) if hint else [])
    invoice = (hint or {}).get("invoice", {"state": "unknown"})
    proposals = (hint or {}).get("proposals", []) if hint else []
    return {
        "link_id": str(link.get("link_id", "")),
        "completed_on": str(link.get("completed_on", "")),
        "contact_id": contact_id,
        "case_id": str(link.get("mail_case_id", "")),
        "visit_id": str(link.get("visit_id", "")),
        "mail_url": _mail_url(str(link.get("mail_case_id", ""))),
        "visit_url": _visit_url(str(link.get("visit_id", ""))),
        "editor_url": editor_url,
        "tasks_url": TASKS_URL,
        "hint": hint,
        "hint_id": str((hint or {}).get("hint_id", "")),
        "reason": str((hint or {}).get("reason_code", "")),
        "days": (hint or {}).get("days_since_completion", ""),
        "evidence": evidence,
        "proposals": proposals,
        "invoice_state": str(invoice.get("state", "unknown")),
    }


@bp.get("")
@bp.get("/")
@login_required
def dashboard():
    """Show the actor's confirmed cases with evidenced hints."""
    _gate()
    actor = _actor()
    root = _root()
    store = CaseLinkStore(root)
    states = HintStates(root)
    today = _now().date().isoformat()
    cards = []
    for link in store.list_links(actor):
        link_id = str(link.get("link_id", ""))
        if not states.is_visible(actor, link_id, today):
            continue
        try:
            hint = FollowupEvaluator(root).evaluate(actor, link_id, _now())
        except (PermissionError, ValueError, KeyError):
            continue
        cards.append(_card(root, actor, link, hint))
    cases = [
        {"id": str(row.get("id", "")), "title": str(row.get("title", ""))}
        for row in MailCaseStore(root).list_cases(actor)
        if isinstance(row, dict)
    ]
    contacts = [
        {
            "id": str(row.get("contact_id", "")),
            "name": str((row.get("fields", {}) or {}).get("display_name", "")),
        }
        for row in ContactStore(root).contacts(actor)
        if isinstance(row, dict)
    ]
    visits = [
        {
            "id": str(row.get("visit_id", "")),
            "title": str(row.get("title", "")),
            "status": str(row.get("status", "")),
            "visit_date": str(row.get("visit_date", "")),
        }
        for row in SiteVisitStore(root).list(actor)
        if isinstance(row, dict)
    ]
    return render_template(
        "contextflow/index.html",
        cards=cards,
        cases=cases,
        contacts=contacts,
        visits=visits,
    )


@bp.post("/links/confirm")
@login_required
def confirm_link():
    """Confirm an assignment from explicit ids and a completion date."""
    _gate()
    actor = _actor()
    form = request.form
    try:
        link = CaseLinkStore(_root()).confirm(
            actor,
            mail_case_id=str(form.get("mail_case_id", "")),
            contact_id=str(form.get("contact_id", "")),
            visit_id=str(form.get("visit_id", "")),
            invoice_id=str(form.get("invoice_id", "") or "") or None,
            completed_on=str(form.get("completed_on", "")),
            now=_now(),
        )
    except PermissionError:
        abort(403)
    except ValueError as exc:
        abort(400, description=str(exc))
    _ = link
    return redirect(url_for("contextflow.dashboard"))


@bp.post("/approve")
@login_required
def approve():
    """Execute one reviewed proposal as a real task (exactly once)."""
    _gate()
    actor = _actor()
    form = request.form
    try:
        approve_task(
            actor,
            str(form.get("link_id", "")),
            str(form.get("expected_hint_id", "")),
            form.get("approved") == "yes",
            _now(),
            _root(),
        )
    except ApprovalStale as exc:
        abort(409, description=str(exc))
    except PermissionError:
        abort(403)
    except ValueError as exc:
        abort(400, description=str(exc))
    return redirect(url_for("contextflow.dashboard"))


@bp.post("/snooze")
@login_required
def snooze():
    """Hide a hint until a chosen date (actor-local, persistent)."""
    _gate()
    actor = _actor()
    form = request.form
    link_id = str(form.get("link_id", ""))
    try:
        CaseLinkStore(_root()).get(actor, link_id)
        HintStates(_root()).set(
            actor, link_id, "snoozed", str(form.get("until", ""))
        )
    except PermissionError:
        abort(403)
    except ValueError as exc:
        abort(400, description=str(exc))
    return redirect(url_for("contextflow.dashboard"))


@bp.post("/dismiss")
@login_required
def dismiss():
    """Hide a hint permanently for the actor (reversible via new evidence)."""
    _gate()
    actor = _actor()
    link_id = str(request.form.get("link_id", ""))
    try:
        CaseLinkStore(_root()).get(actor, link_id)
        HintStates(_root()).set(actor, link_id, "dismissed")
    except PermissionError:
        abort(403)
    except ValueError as exc:
        abort(400, description=str(exc))
    return redirect(url_for("contextflow.dashboard"))
