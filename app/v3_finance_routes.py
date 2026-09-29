"""Optional V3 business-document lifecycle routes."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .access_control import has_feature
from .auth import login_required
from .business_document_generation import invoice
from .contact_store import ContactStore
from .v3_capabilities import enabled
from .v3_finance import FinanceLifecycleStore, GENERIC_KINDS, KINDS, SUCCESSORS, TRANSITIONS


bp = Blueprint(
    "v3_finance",
    __name__,
    url_prefix="/documents/business/lifecycle-v3",
)


@bp.app_context_processor
def finance_capability_context():
    return {"v3_finance_enabled": enabled("v3.finance")}


def _require_finance() -> None:
    if not enabled("v3.finance"):
        abort(404)
    if not has_feature(g.user, "documents"):
        abort(403)


def _actor() -> str:
    return str(g.user["username"])


def _store() -> FinanceLifecycleStore:
    return FinanceLifecycleStore(current_app.config["DOCUMENT_ROOT"])


def _can_manage_contact(contact_id: str) -> bool:
    if not contact_id:
        return bool(g.user["is_admin"])
    return ContactStore(current_app.config["DOCUMENT_ROOT"]).can_manage(
        contact_id,
        _actor(),
    )


def _require_document(lifecycle_id: str):
    try:
        row = _store().get(lifecycle_id)
    except LookupError:
        abort(404)
    if row.contact_id and not _can_manage_contact(row.contact_id):
        abort(403)
    if row.project_id and not has_feature(g.user, "projects"):
        abort(403)
    return row


@bp.get("")
@login_required
def index():
    if not has_feature(g.user, "documents"):
        abort(403)
    if not enabled("v3.finance"):
        return redirect(url_for("contact_audit.business_documents.invoice_overview"))
    rows = [
        row
        for row in _store().list(limit=300)
        if (not row.contact_id or _can_manage_contact(row.contact_id))
        and (not row.project_id or has_feature(g.user, "projects"))
    ]
    return render_template(
        "documents/v3_finance_lifecycle.html",
        rows=rows,
        generic_kinds=sorted(GENERIC_KINDS),
    )


@bp.post("")
@login_required
def create():
    _require_finance()
    kind = request.form.get("kind", "").strip()
    if kind not in GENERIC_KINDS:
        abort(400)
    contact_id = request.form.get("contact_id", "").strip()
    project_id = request.form.get("project_id", "").strip()
    if contact_id and not _can_manage_contact(contact_id):
        abort(403)
    if project_id and not has_feature(g.user, "projects"):
        abort(403)
    working = {
        "subject": request.form.get("subject", "").strip()[:300],
        "description": request.form.get("description", "").strip()[:4000],
        "currency": request.form.get("currency", "EUR").strip().upper()[:3],
        "amount": request.form.get("amount", "").strip()[:40],
    }
    try:
        row = _store().create(
            kind,
            _actor(),
            contact_id=contact_id,
            project_id=project_id,
            title=request.form.get("title", "") or working["subject"] or kind,
            working=working,
        )
    except ValueError as exc:
        flash(f"Geschäftsdokument nicht angelegt: {exc}")
        return redirect(url_for("v3_finance.index"))
    return redirect(url_for("v3_finance.detail", lifecycle_id=row.lifecycle_id))


@bp.get("/<lifecycle_id>")
@login_required
def detail(lifecycle_id: str):
    _require_finance()
    row = _require_document(lifecycle_id)
    attempts = []
    legacy_invoice = None
    if row.kind == "invoice" and row.external_id:
        try:
            legacy_invoice = invoice(
                current_app.config["DOCUMENT_ROOT"],
                row.external_id,
            )
        except ValueError:
            legacy_invoice = None
        attempt = _store().latest_attempt(row.external_id)
        if attempt is not None:
            attempts.append(attempt)
    return render_template(
        "documents/v3_finance_detail.html",
        item=row,
        history=_store().history(row.lifecycle_id),
        successors=_store().successors(row.lifecycle_id),
        allowed_transitions=sorted(
            TRANSITIONS.get(row.kind, {}).get(row.status, set())
        ),
        successor_kinds=sorted(SUCCESSORS.get(row.kind, set())),
        legacy_invoice=legacy_invoice,
        attempts=attempts,
    )


@bp.post("/<lifecycle_id>/draft")
@login_required
def update_draft(lifecycle_id: str):
    _require_finance()
    row = _require_document(lifecycle_id)
    if row.kind not in GENERIC_KINDS:
        abort(400)
    working = dict(row.working)
    working.update({
        "subject": request.form.get("subject", "").strip()[:300],
        "description": request.form.get("description", "").strip()[:4000],
        "currency": request.form.get("currency", "EUR").strip().upper()[:3],
        "amount": request.form.get("amount", "").strip()[:40],
    })
    try:
        _store().update_draft(
            lifecycle_id,
            _actor(),
            title=request.form.get("title", ""),
            working=working,
        )
        flash("Entwurf gespeichert.")
    except ValueError as exc:
        flash(f"Entwurf nicht gespeichert: {exc}")
    return redirect(url_for("v3_finance.detail", lifecycle_id=lifecycle_id))


@bp.post("/<lifecycle_id>/transition")
@login_required
def transition(lifecycle_id: str):
    _require_finance()
    _require_document(lifecycle_id)
    try:
        _store().transition(
            lifecycle_id,
            request.form.get("status", ""),
            _actor(),
            detail={"source": "web"},
        )
        flash("Status geändert.")
    except ValueError as exc:
        flash(f"Status nicht geändert: {exc}")
    return redirect(url_for("v3_finance.detail", lifecycle_id=lifecycle_id))


@bp.post("/<lifecycle_id>/convert")
@login_required
def convert(lifecycle_id: str):
    _require_finance()
    source = _require_document(lifecycle_id)
    target_kind = request.form.get("target_kind", "").strip()
    if target_kind == "invoice":
        invoice_id = request.form.get("invoice_id", "").strip()
        try:
            invoice_row = invoice(current_app.config["DOCUMENT_ROOT"], invoice_id)
        except ValueError:
            flash("Der angegebene Rechnungsentwurf existiert nicht.")
            return redirect(url_for("v3_finance.detail", lifecycle_id=lifecycle_id))
        if not _can_manage_contact(str(invoice_row.get("contact_id", ""))):
            abort(403)
    else:
        invoice_id = ""
    try:
        created = _store().convert(
            lifecycle_id,
            target_kind,
            _actor(),
            external_id=invoice_id,
        )
        flash("Nachfolger angelegt und mit dem Vorgänger verknüpft.")
        return redirect(
            url_for("v3_finance.detail", lifecycle_id=created.lifecycle_id)
        )
    except ValueError as exc:
        flash(f"Konvertierung nicht möglich: {exc}")
        return redirect(url_for("v3_finance.detail", lifecycle_id=source.lifecycle_id))


@bp.post("/<lifecycle_id>/recover")
@login_required
def recover(lifecycle_id: str):
    _require_finance()
    row = _require_document(lifecycle_id)
    if row.kind != "invoice" or not row.external_id:
        abort(400)
    try:
        legacy = invoice(current_app.config["DOCUMENT_ROOT"], row.external_id)
        recovered = _store().recover_finalization(
            legacy,
            _actor(),
            force=bool(g.user["is_admin"]) and request.form.get("force") == "1",
        )
        flash(f"Finalisierungszustand auf {recovered.status} abgeglichen.")
    except ValueError as exc:
        flash(f"Recovery nicht durchgeführt: {exc}")
    return redirect(url_for("v3_finance.detail", lifecycle_id=lifecycle_id))
