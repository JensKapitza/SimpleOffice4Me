"""Agent demo page rendering (ContextFlow).

View assembly for /contextflow/agent: readable case titles, verified
evidence deep links and honest provider status. All reads stay
actor-scoped; no writes and no model calls happen here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

from flask import render_template

from ..contact_store import ContactStore
from ..mail_case_store import MailCaseStore
from ..site_visit_store import SiteVisitStore
from .case_links import CaseLinkStore
from .contracts import (
    INVOICE_EDITOR_URL,
    MAIL_CASE_URL,
    TASKS_URL,
    VISIT_URL,
)
from .demo import is_verified_demo_root
from .vision import META_MODEL_API_MODEL, MetaVisionProvider


def _mail_url(case_id: str) -> str:
    return MAIL_CASE_URL.format(case_id=quote(case_id, safe=""))


def _visit_url(visit_id: str) -> str:
    return VISIT_URL.format(visit_id=quote(visit_id, safe=""))


def _case_titles(actor: str, root: Path) -> dict[str, str]:
    """Readable mail-case titles for the actor's own cases."""
    return {
        str(row.get("id", "")): str(row.get("title", ""))
        for row in MailCaseStore(root).list_cases(actor)
        if isinstance(row, dict)
    }


def _agent_links(actor: str, root: Path) -> list[dict[str, str]]:
    """Confirmed cases the actor may analyse (rights-checked)."""
    titles = _case_titles(actor, root)
    cards = []
    for link in CaseLinkStore(root).list_links(actor):
        if not isinstance(link, dict):
            continue
        link_id = str(link.get("link_id", ""))
        case_id = str(link.get("mail_case_id", ""))
        if not link_id or not case_id:
            continue
        cards.append(
            {
                "link_id": link_id,
                "case_id": case_id,
                "case_title": titles.get(case_id, ""),
                "link_short": link_id[:8],
                "case_short": case_id[:8],
            }
        )
    return cards


def _evidence_links(
    actor: str,
    root: Path,
    link: dict[str, Any],
    ref_ids: list[str],
    editor_url: str,
) -> list[dict[str, str]]:
    """Map validated evidence ids to labels plus verified deep links.

    Only ids of this very link are linked, and only to routes that
    exist (mail case, visit incl. findings anchor, invoice editor);
    everything else renders as plain labeled text, never a guess.
    """
    case_id = str(link.get("mail_case_id", ""))
    contact_id = str(link.get("contact_id", ""))
    visit_id = str(link.get("visit_id", ""))
    invoice_id = str(link.get("invoice_id", "") or "")
    mail_url = _mail_url(case_id) if case_id else ""
    visit_url = _visit_url(visit_id) if visit_id else ""
    finding_ids: set[str] = set()
    asset_ids: set[str] = set()
    if visit_id:
        try:
            record = SiteVisitStore(root).get(visit_id, actor)
        except (PermissionError, ValueError, KeyError):
            record = {}
        if isinstance(record, dict):
            for item in record.get("findings", []) or []:
                if isinstance(item, dict) and item.get("finding_id"):
                    finding_ids.add(str(item["finding_id"]))
            for asset in record.get("assets", []) or []:
                if isinstance(asset, dict) and asset.get("asset_id"):
                    asset_ids.add(str(asset["asset_id"]))
    rows = []
    for ref in ref_ids or []:
        rid = str(ref or "")
        if not rid:
            continue
        short = rid[:8]
        if rid == case_id and mail_url:
            rows.append({"id": rid, "label": f"Fall {short}", "url": mail_url})
        elif rid == visit_id and visit_url:
            rows.append(
                {"id": rid, "label": f"Ortstermin {short}", "url": visit_url}
            )
        elif rid == contact_id and contact_id:
            rows.append({"id": rid, "label": f"Kontakt {short}", "url": ""})
        elif invoice_id and rid == invoice_id:
            rows.append(
                {
                    "id": rid,
                    "label": f"Rechnung {short}",
                    "url": editor_url,
                }
            )
        elif rid in finding_ids and visit_url:
            rows.append(
                {
                    "id": rid,
                    "label": f"Befund {short}",
                    "url": visit_url + "#findings",
                }
            )
        elif rid in asset_ids:
            rows.append({"id": rid, "label": f"Gerät {short}", "url": ""})
        else:
            rows.append({"id": rid, "label": f"Beleg {short}", "url": ""})
    return rows


def _agent_context(root: Path, settings: dict[str, Any]) -> dict[str, Any]:
    """Honest provider/model/key/root status for the agent page."""
    provider = str(settings.get("provider", "local") or "local").strip().lower()
    key_present = bool(str(settings.get("meta_api_key", "") or ""))
    server_consent = settings.get("meta_consent") is True
    return {
        "provider": provider,
        "provider_label": MetaVisionProvider.label,
        "model_id": META_MODEL_API_MODEL,
        "key_present": key_present,
        "server_consent": server_consent,
        "demo_root_ok": is_verified_demo_root(root),
        "meta_ready": provider == "meta" and server_consent and key_present,
    }


def render_agent(
    actor: str,
    root: Path,
    settings: dict[str, Any],
    result: dict[str, Any] | None,
    error: str | None,
    status: int = 200,
    preselected: str = "",
    run_seconds: float | None = None,
):
    """Render the agent page with honest status codes (no bare aborts)."""
    follow = None
    evidence: list[dict[str, str]] = []
    result_case: dict[str, str] | None = None
    if result is not None:
        try:
            link = CaseLinkStore(root).get(actor, str(result.get("link_id", "")))
        except (PermissionError, ValueError, KeyError):
            link = None
        if link is not None:
            contact_id = str(link.get("contact_id", ""))
            can_manage = False
            try:
                can_manage = bool(
                    ContactStore(root).can_manage(contact_id, actor)
                )
            except (PermissionError, ValueError, KeyError):
                can_manage = False
            editor_url = (
                INVOICE_EDITOR_URL.format(contact_id=quote(contact_id, safe=""))
                if can_manage and contact_id
                else ""
            )
            follow = {
                "mail_url": _mail_url(str(link.get("mail_case_id", ""))),
                "tasks_url": TASKS_URL,
                "editor_url": editor_url,
            }
            case_id = str(link.get("mail_case_id", ""))
            result_case = {
                "id": case_id,
                "title": _case_titles(actor, root).get(case_id, ""),
            }
            refs = result.get("evidence_ref_ids", [])
            evidence = _evidence_links(
                actor,
                root,
                link,
                [str(ref) for ref in refs if isinstance(refs, list)],
                editor_url,
            )
    return (
        render_template(
            "contextflow/agent.html",
            cards=_agent_links(actor, root),
            result=result,
            error=error,
            follow=follow,
            evidence=evidence,
            result_case=result_case,
            preselected=preselected,
            run_seconds=run_seconds,
            **_agent_context(root, settings),
        ),
        status,
    )
