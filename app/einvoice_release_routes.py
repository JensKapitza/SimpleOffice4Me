"""E-invoice release routes: precheck, release, protocol and XML download (AP6)."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

from flask import Blueprint, abort, flash, g, redirect, render_template, send_file, url_for

from .access_control import has_feature
from .auth import login_required
from .business_document_generation import (
    _actor,
    _root,
    _safe_filename,
)
from .einvoice_release import (
    precheck_draft,
    read_release_protocol,
    release_checklist,
    release_einvoice,
)
from .invoice_access import _managed_invoice

bp = Blueprint("einvoice_release", __name__, url_prefix="/documents/business")


def _invoice_root() -> Path:
    return Path(_root())


def _managed_or_404(root: Path, invoice_id: str, actor: str) -> dict:
    """Managed invoice loader that treats a missing store as 'not found'."""
    try:
        return _managed_invoice(root, invoice_id, actor)
    except OSError:
        abort(404)


@bp.get("/invoices/<invoice_id>/einvoice/precheck")
@login_required
def einvoice_precheck(invoice_id: str):
    """Field-level dry run (drafts) or full checklist (finalized). No writes."""
    if not has_feature(g.user, "documents"):
        abort(403)
    root, actor = _invoice_root(), _actor()
    row = _managed_or_404(root, invoice_id, actor)
    if str(row.get("status", "")) in {"draft", "finalizing", ""}:
        report = precheck_draft(row)
    else:
        report = release_checklist(root, row)
    return render_template(
        "documents/einvoice_precheck.html",
        invoice=row,
        report=report,
        released=bool((row.get("einvoice_release") or {}).get("released_at")),
    )


@bp.post("/invoices/<invoice_id>/einvoice/release")
@login_required
def einvoice_release(invoice_id: str):
    """Joint release action: gate + artifacts + protocol, then back to detail."""
    if not has_feature(g.user, "documents"):
        abort(403)
    root, actor = _invoice_root(), _actor()
    row = _managed_or_404(root, invoice_id, actor)
    detail = url_for(
        "contact_audit.business_documents.invoice_detail", invoice_id=invoice_id)
    try:
        result = release_einvoice(root, row, actor)
    except ValueError as exc:
        flash(str(exc)[:500])
        return redirect(url_for(".einvoice_precheck", invoice_id=invoice_id))
    if result.get("idempotent_replay"):
        flash("E-Rechnung war bereits freigegeben – gespeichertes Ergebnis übernommen (keine neue Nummer)."
              if g.language != "en" else
              "E-invoice was already released – stored result reused (no new number).")
    else:
        flash("E-Rechnung freigegeben: XRechnung-XML und Prüfprotokoll sind archiviert."
              if g.language != "en" else
              "E-invoice released: XRechnung XML and validation protocol are archived.")
    return redirect(detail)


@bp.get("/invoices/<invoice_id>/einvoice/protocol")
@login_required
def einvoice_protocol(invoice_id: str):
    """Read-only view of the stored validation protocol (AP5/AP6)."""
    if not has_feature(g.user, "documents"):
        abort(403)
    root, actor = _invoice_root(), _actor()
    row = _managed_or_404(root, invoice_id, actor)
    try:
        protocol = read_release_protocol(root, invoice_id)
    except ValueError:
        abort(404)
    return render_template(
        "documents/einvoice_protocol.html", invoice=row, protocol=protocol)


@bp.get("/invoices/<invoice_id>/einvoice/xml")
@login_required
def einvoice_xml_download(invoice_id: str):
    """Serve the released XRechnung XML after a hash check (T13)."""
    if not has_feature(g.user, "documents"):
        abort(403)
    root, actor = _invoice_root(), _actor()
    row = _managed_or_404(root, invoice_id, actor)
    release = row.get("einvoice_release") or {}
    if not release.get("released_at"):
        abort(404)
    try:
        protocol = read_release_protocol(root, invoice_id)
    except ValueError:
        abort(404)
    expected = str(((protocol.get("artifacts", {}) or {}).get(
        "xrechnung_xml", {}) or {}).get("sha256", ""))
    xml_file = str(release.get("xml_file", "") or "")
    if not expected or not xml_file:
        abort(404)
    from .business_document_generation import CONTROL_DIR, INVOICE_DIR
    from .safe_paths import resolve_file_under

    try:
        path = resolve_file_under(
            root, str(Path(CONTROL_DIR) / INVOICE_DIR / xml_file))
        payload = path.read_bytes()
    except (OSError, ValueError):
        abort(404)
    if hashlib.sha256(payload).hexdigest() != expected:
        flash("Archivierte XRechnung weicht vom Protokoll ab (Hash-Fehler) – Download blockiert."
              if g.language != "en" else
              "Archived XRechnung differs from the protocol (hash mismatch) – download blocked.")
        return redirect(url_for(
            "contact_audit.business_documents.invoice_detail",
            invoice_id=invoice_id))
    number = _safe_filename(str(row.get("invoice_number", invoice_id)))
    return send_file(
        io.BytesIO(payload),
        as_attachment=True,
        download_name=f"XRechnung-{number}.xml",
        mimetype="application/xml",
    )

