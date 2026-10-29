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
from ..document_store import DocumentStore
from ..mail_case_store import MailCaseStore
from ..object_vision import ocr_availability
from ..site_visit_store import SiteVisitStore
from .actions import ApprovalStale, approve_task
from .case_links import BERLIN, CaseLinkStore
from .contracts import MAIL_CASE_URL, TASKS_URL, VISIT_URL
from .doc_tags import (
    DocumentNotFoundError,
    apply_tag_confirmation,
    suggest_image_for_document,
    suggest_tags_for_document,
)
from .evaluator import FollowupEvaluator
from .contact_suggest import MAX_PHOTOS as MAX_CONTACT_PHOTOS
from .contact_suggest import suggest_contact_fields
from .hints import HintStates
from .inventory_match import (
    AmbiguousMatchError,
    EmptyIdentifierError,
    IdentifierTooLongError,
    MalformedGtinError,
    NoMatchError,
    TargetMismatchError,
    attach_photo,
    find_exact_matches,
    resolve_unique_target,
)
from .peer_stock import peer_availability
from .vision import (
    InferenceTimeoutError,
    InferenceUnavailable,
    confirm_draft,
    ml_status,
    prepare_draft,
    suggest_inventory_object,
    validate_image,
)

bp = Blueprint("contextflow", __name__, url_prefix="/contextflow")
INVOICE_EDITOR_URL = "/documents/business/contacts/{contact_id}/invoice"


def _enabled() -> bool:
    """ContextFlow is off unless explicitly enabled via config or env."""
    return bool(
        current_app.config.get("CONTEXTFLOW_ENABLED", False)
        or os.environ.get("CONTEXTFLOW_ENABLED") == "1"
    )


@bp.app_context_processor
def contextflow_nav_context() -> dict[str, bool]:
    """Expose the flag for navigation; missing context fails closed."""
    try:
        return {"contextflow_enabled": _enabled()}
    except RuntimeError:
        return {"contextflow_enabled": False}


def _gate() -> None:
    if not _enabled():
        abort(404)


def _actor() -> str:
    user = g.get("user")
    name = ""
    if user is not None:
        try:
            name = str(user["username"] or "")
        except (KeyError, IndexError, TypeError):
            name = ""
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
    root: Path,
    actor: str,
    link: dict[str, Any],
    hint: dict[str, Any] | None,
    case_title: str = "",
) -> dict[str, Any]:
    """Assemble one dashboard card with safe links and editor deep link."""
    contact_id = str(link.get("contact_id", ""))
    visit_id = str(link.get("visit_id", ""))
    case_id = str(link.get("mail_case_id", ""))
    invoice = (hint or {}).get("invoice", {"state": "unknown"})
    invoice_state = str(invoice.get("state", "unknown"))
    invoice_display = (
        "unbekannt"
        if invoice_state == "unknown"
        else ("bestätigt zugeordnet" if invoice_state == "linked" else invoice_state)
    )
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
    proposals = (hint or {}).get("proposals", []) if hint else []
    devices: list[dict[str, str]] = []
    for row in (hint or {}).get("device_context", []) or []:
        if not isinstance(row, dict) or not row.get("asset_id"):
            continue
        finding_ids = [
            str(item)
            for item in row.get("finding_ids", []) or []
            if str(item).strip()
        ]
        devices.append(
            {
                "asset_id": str(row.get("asset_id", "")),
                "name": str(row.get("name", "") or row.get("asset_id", "")),
                "kind": str(row.get("kind", "")),
                "url": str(row.get("url", "") or ""),
                "finding_ids": ", ".join(finding_ids[:4]),
                "finding_count": str(len(finding_ids)),
            }
        )
    primary_device = devices[0] if devices else None
    return {
        "link_id": str(link.get("link_id", "")),
        "completed_on": str(link.get("completed_on", "")),
        "contact_id": contact_id,
        "case_id": case_id,
        "case_title": case_title,
        "visit_id": visit_id,
        "mail_url": _mail_url(case_id),
        "visit_url": _visit_url(visit_id),
        "editor_url": editor_url,
        "tasks_url": TASKS_URL,
        "hint": hint,
        "hint_id": str((hint or {}).get("hint_id", "")),
        "reason": str((hint or {}).get("reason_code", "")),
        "days": (hint or {}).get("days_since_completion", ""),
        "evidence": evidence,
        "proposals": proposals,
        "invoice_state": invoice_state,
        "invoice_display": invoice_display,
        "devices": devices,
        "primary_device": primary_device,
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
    links = store.list_links(actor)
    linked_cases = {
        str(link.get("mail_case_id", "")) for link in links if isinstance(link, dict)
    }
    linked_contacts = {
        str(link.get("contact_id", "")) for link in links if isinstance(link, dict)
    }
    linked_visits = {
        str(link.get("visit_id", "")) for link in links if isinstance(link, dict)
    }
    case_titles = {
        str(row.get("id", "")): str(row.get("title", ""))
        for row in MailCaseStore(root).list_cases(actor)
        if isinstance(row, dict)
    }
    cards = []
    for link in links:
        link_id = str(link.get("link_id", ""))
        if not states.is_visible(actor, link_id, today):
            continue
        try:
            hint = FollowupEvaluator(root).evaluate(actor, link_id, _now())
        except (PermissionError, ValueError, KeyError):
            continue
        cards.append(
            _card(
                root,
                actor,
                link,
                hint,
                case_title=case_titles.get(str(link.get("mail_case_id", "")), ""),
            )
        )
    cases = [
        {"id": case_id, "title": title}
        for case_id, title in case_titles.items()
        if case_id and case_id not in linked_cases
    ]
    contacts = [
        {
            "id": str(row.get("contact_id", "")),
            "name": str((row.get("fields", {}) or {}).get("display_name", "")),
        }
        for row in ContactStore(root).contacts(actor)
        if isinstance(row, dict)
        and str(row.get("contact_id", "")) not in linked_contacts
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
        and str(row.get("visit_id", "")) not in linked_visits
    ]
    vision_assets: list[dict[str, str]] = []
    for row in SiteVisitStore(root).list(actor):
        if not isinstance(row, dict):
            continue
        vid = str(row.get("visit_id", ""))
        try:
            record = SiteVisitStore(root).get(vid, actor)
        except (PermissionError, ValueError, KeyError):
            continue
        for asset in record.get("assets", []) or []:
            if not isinstance(asset, dict) or not asset.get("asset_id"):
                continue
            vision_assets.append(
                {
                    "visit_id": vid,
                    "visit_title": str(record.get("title", "")),
                    "asset_id": str(asset.get("asset_id", "")),
                    "name": str(asset.get("name", "")),
                    "kind": str(asset.get("kind", "")),
                }
            )
    vision_labels = {
        "local": "Meta-Modell lokal via Ollama",
        "mock": "Demo-Mock (keine echte Analyse)",
        "meta": "Meta Muse Spark 1.3 (Meta Model API · nur synthetische Demo + Einwilligung)",
    }
    vision_visits = [
        {"id": str(row.get("visit_id", "")), "title": str(row.get("title", ""))}
        for row in SiteVisitStore(root).list(actor)
        if isinstance(row, dict)
    ]
    vision_provider = str(
        current_app.config.get("CONTEXTFLOW_VISION_PROVIDER", "local") or "local"
    ).lower()
    return render_template(
        "contextflow/index.html",
        cards=cards,
        cases=cases,
        contacts=contacts,
        visits=visits,
        vision_visits=vision_visits,
        vision_assets=vision_assets,
        vision_label=vision_labels.get(vision_provider, vision_labels["local"]),
        back_to_inventory=request.args.get("origin", "") == "inventory",
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
            confirmed_asset_id=str(form.get("confirmed_asset_id", "") or ""),
            confirm_device=form.get("confirm_device") == "yes",
        )
    except ApprovalStale as exc:
        abort(409, description=str(exc))
    except PermissionError:
        abort(403)
    except ValueError as exc:
        abort(400, description=str(exc))
    return redirect(url_for("contextflow.dashboard"))


def _vision_settings() -> dict[str, Any]:
    """Vision backend settings; Meta stays doubly gated (flag + checkbox)."""
    return {
        "provider": current_app.config.get("CONTEXTFLOW_VISION_PROVIDER", "local"),
        "ollama_url": current_app.config.get(
            "CONTEXTFLOW_OLLAMA_URL", "http://127.0.0.1:11434"
        ),
        "ollama_model": current_app.config.get("CONTEXTFLOW_OLLAMA_MODEL", ""),
        "ollama_timeout": current_app.config.get("CONTEXTFLOW_OLLAMA_TIMEOUT", 60),
        "meta_consent": bool(current_app.config.get("CONTEXTFLOW_META_CONSENT", False)),
        "meta_api_key": os.environ.get("MODEL_API_KEY", ""),
        "meta_endpoint": current_app.config.get("CONTEXTFLOW_META_ENDPOINT", ""),
        "meta_model": current_app.config.get("CONTEXTFLOW_META_MODEL", ""),
    }


@bp.get("/ml-status")
@login_required
def ml_status_route():
    """Machine-readable ML backend status for the header badge (JSON).

    Login is required, but no object rights: the payload carries
    availability booleans and configured model names only — never API
    keys, credentials, or tracebacks.
    """
    _gate()
    status = ml_status(_vision_settings())
    status["ocr"] = ocr_availability()
    return {"ok": True, **status}, 200


@bp.post("/inventory-suggest")
@login_required
def inventory_suggest():
    """Suggest an inventory object name from a photo (JSON, nothing stored).

    No consent element exists on the inventory form, so cloud inference is
    always refused here; only real local backends answer, while mock is
    refused with 503 (no invented classifications). Saving still happens
    exclusively via the normal inventory form.
    """
    _gate()
    _actor()
    form = request.form
    upload = request.files.get("photo")
    if upload is None:
        abort(400, description="a photo file is required")
    try:
        image = validate_image(upload.filename, upload.content_type, upload.stream)
        result = suggest_inventory_object(
            image,
            str(upload.content_type or "").strip().lower(),
            hint={
                "barcode": str(form.get("barcode", "") or ""),
                "isbn": str(form.get("isbn", "") or ""),
            },
            settings=_vision_settings(),
            consent_meta=False,
        )
    except PermissionError:
        abort(403)
    except InferenceUnavailable as exc:
        payload: dict[str, object] = {"error": str(exc)}
        if isinstance(exc, InferenceTimeoutError):
            payload["timeout"] = True
        return payload, 503
    except ValueError as exc:
        abort(400, description=str(exc))
    return result, 200


@bp.post("/inventory-match")
@login_required
def inventory_match():
    """Exact identifier lookup over the local catalog (JSON, read-only).

    Only canonical ISBN or exact barcode/GTIN matches; titles are never
    consulted. Ambiguity is reported, never resolved silently.
    """
    _gate()
    _actor()
    try:
        kind, matches = find_exact_matches(_root(), request.form.get("identifier", ""))
    except ValueError as exc:
        abort(400, description=str(exc))
    if not matches:
        return {"status": "none", "kind": kind, "matches": []}, 200
    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "kind": kind,
            "count": len(matches),
            "matches": matches,
        }, 200
    return {"status": "unique", "kind": kind, "matches": matches}, 200


@bp.post("/inventory-attach")
@login_required
def inventory_attach():
    """Attach one candidate photo to one exactly-matched item (JSON).

    The identifier is re-resolved server-side and must uniquely point at
    the claimed target; anything else is refused. Never creates objects,
    never calls external services.
    """
    _gate()
    actor = _actor()
    upload = request.files.get("photo")
    if upload is None or not getattr(upload, "filename", ""):
        abort(400, description="Kein Foto ausgewählt")
    try:
        target = resolve_unique_target(
            _root(), request.form.get("identifier", ""), request.form.get("item_id", "")
        )
    except (EmptyIdentifierError, IdentifierTooLongError, MalformedGtinError) as exc:
        abort(400, description=str(exc))
    except NoMatchError as exc:
        abort(404, description=str(exc))
    except (AmbiguousMatchError, TargetMismatchError) as exc:
        abort(409, description=str(exc))
    try:
        photo = attach_photo(_root(), target["object_id"], upload, actor)
    except ValueError as exc:
        abort(400, description=str(exc))
    return {"ok": True, "object_id": target["object_id"], "filename": photo["filename"]}, 200


@bp.post("/contact-suggest")
@login_required
def contact_suggest():
    """Suggest contact fields from user text and photos (JSON, nothing stored).

    Photos are OCR-read locally; exactly one text-only Meta call
    structures the sources (photo bytes never leave the machine). Local
    Ollama answers by default; the Meta Model API path needs the
    server-side Meta switch plus API key and the explicit per-request
    consent checkbox, otherwise cloud inference is refused here. Mock
    answers are refused with 503 (no invented contact data). Saving
    still happens exclusively via the normal contact form.
    """
    _gate()
    _actor()
    uploads = [
        item
        for item in request.files.getlist("photos")
        if item is not None and getattr(item, "filename", "")
    ]
    if len(uploads) > MAX_CONTACT_PHOTOS:
        return {
            "ok": False,
            "error": f"Bitte höchstens {MAX_CONTACT_PHOTOS} Fotos auswählen.",
        }, 400
    photos: list[dict[str, Any]] = []
    for index, upload in enumerate(uploads, start=1):
        try:
            photos.append(
                {
                    "data": validate_image(
                        upload.filename, upload.content_type, upload.stream
                    ),
                    "mime": str(upload.content_type or "").strip().lower(),
                }
            )
        except ValueError:
            return {
                "ok": False,
                "error": (
                    f"Foto {index}: nur JPEG, PNG oder WebP bis 5 MiB. "
                    "Bitte ein lesbares Foto wählen."
                ),
            }, 400
    try:
        result = suggest_contact_fields(
            text=request.form.get("contact_text", ""),
            photos=photos,
            settings=_vision_settings(),
            consent_meta=request.form.get("consent_meta") == "yes",
            root=_root(),
        )
    except PermissionError:
        abort(403)
    except InferenceUnavailable as exc:
        payload: dict[str, object] = {"ok": False, "error": str(exc)}
        if isinstance(exc, InferenceTimeoutError):
            payload["timeout"] = True
        return payload, 503
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}, 400
    return {"ok": True, **result}, 200


@bp.post("/doc-tag-suggest")
@login_required
def doc_tag_suggest():
    """Propose tags for one document from metadata + extracted text (JSON).

    Nothing is stored. No consent element exists on the smart-folder page,
    so cloud inference is always refused here; only real local backends
    answer, while mock is refused with 503 (no invented tags). Image
    notes arrive through the separate /doc-image-suggest call, so text
    tags never wait for vision.
    """
    _gate()
    actor = _actor()
    document_id = str(request.form.get("document_id", "") or "").strip()
    if not document_id:
        return {"ok": False, "error": "Dokumentreferenz fehlt."}, 400
    try:
        document = DocumentStore(_root()).get_document(document_id)
    except ValueError:
        abort(404)
    try:
        result = suggest_tags_for_document(
            document=document,
            settings=_vision_settings(),
            root=_root(),
            actor=actor,
            folder_hint=str(request.form.get("folder", "") or "").strip()[:200],
        )
    except PermissionError:
        abort(403)
    except InferenceUnavailable as exc:
        payload: dict[str, object] = {"ok": False, "error": str(exc)}
        if isinstance(exc, InferenceTimeoutError):
            payload["timeout"] = True
        return payload, 503
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}, 400
    return {"ok": True, **result}, 200


@bp.post("/doc-image-suggest")
@login_required
def doc_image_suggest():
    """Describe one image document through the local vision model (JSON).

    Fired in parallel with /doc-tag-suggest so text tags never wait
    for vision. Returns {"image": null} for non-image documents (no
    error); unusable images answer 400, a missing or too-slow local
    model 503. Nothing is stored.
    """
    _gate()
    actor = _actor()
    document_id = str(request.form.get("document_id", "") or "").strip()
    if not document_id:
        return {"ok": False, "error": "Dokumentreferenz fehlt."}, 400
    try:
        document = DocumentStore(_root()).get_document(document_id)
    except ValueError:
        abort(404)
    try:
        result = suggest_image_for_document(
            document=document,
            settings=_vision_settings(),
            root=_root(),
            actor=actor,
        )
    except PermissionError:
        abort(403)
    except InferenceUnavailable as exc:
        payload: dict[str, object] = {"ok": False, "error": str(exc)}
        if isinstance(exc, InferenceTimeoutError):
            payload["timeout"] = True
        return payload, 503
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}, 400
    if result is None:
        return {"ok": True, "image": None}, 200
    return {"ok": True, **result}, 200


@bp.post("/doc-tag-confirm")
@login_required
def doc_tag_confirm():
    """Apply explicitly confirmed tag suggestions through the normal tag path."""
    _gate()
    actor = _actor()
    document_id = str(request.form.get("document_id", "") or "").strip()
    if not document_id:
        return {"ok": False, "error": "Dokumentreferenz fehlt."}, 400
    selected = request.form.getlist("tags")
    if len(selected) == 1 and "," in selected[0]:
        selected = selected[0].split(",")
    try:
        result = apply_tag_confirmation(
            root=_root(), actor=actor, document_id=document_id, tags=selected
        )
    except DocumentNotFoundError:
        abort(404)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}, 400
    return {"ok": True, **result}, 200


@bp.post("/peer-stock-check")
@login_required
def peer_stock_check():
    """Ask opt-in friends' shared catalogs for one identifier (JSON).

    Read-only and local: only friends (trust edge plus enabled peer
    record plus documents-receive opt-in) are consulted, no network
    is used, no content is stored or forwarded. Friends without a
    valid index are listed as not_synced/error, never as "no hit".
    """
    _gate()
    actor = _actor()
    try:
        result = peer_availability(
            root=_root(), actor=actor, identifier=request.form.get("identifier", "")
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}, 400
    return {"ok": True, **result}, 200


@bp.post("/suggest")
@login_required
def suggest():
    """Turn an uploaded photo into a reviewable draft (nothing stored)."""
    _gate()
    actor = _actor()
    form = request.form
    upload = request.files.get("photo")
    try:
        draft = prepare_draft(
            _root(),
            actor,
            visit_id=str(form.get("visit_id", "")),
            asset_id=str(form.get("asset_id", "") or ""),
            filename=upload.filename if upload else "",
            content_type=upload.content_type if upload else "",
            stream=upload.stream if upload else None,
            consent_meta=form.get("consent_meta") == "yes",
            settings=_vision_settings(),
        )
    except PermissionError:
        abort(403)
    except ValueError as exc:
        abort(400, description=str(exc))
    return render_template("contextflow/suggest.html", draft=draft)


@bp.post("/suggest/confirm")
@login_required
def confirm_suggestion():
    """Create the reviewed finding exactly as confirmed (explicit only)."""
    _gate()
    actor = _actor()
    form = request.form
    if form.get("approved") != "yes":
        abort(400, description="explicit approval is required")
    confidence: object = form.get("confidence", "0")
    try:
        confidence = int(str(confidence))
    except (TypeError, ValueError):
        pass
    try:
        created = confirm_draft(
            _root(),
            actor,
            visit_id=str(form.get("visit_id", "")),
            asset_id=str(form.get("asset_id", "") or ""),
            title=str(form.get("title", "")),
            description=str(form.get("description", "")),
            evidence_class=str(form.get("evidence_class", "")),
            confidence=confidence,
            expected_fingerprint=str(form.get("expected_fingerprint", "")),
        )
    except ApprovalStale as exc:
        abort(409, description=str(exc))
    except PermissionError:
        abort(403)
    except ValueError as exc:
        abort(400, description=str(exc))
    return redirect(_visit_url(str(created["visit_id"])) + "#findings")


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
