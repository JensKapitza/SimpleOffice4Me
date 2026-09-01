"""Web workflow for compact cash-basis EÜR bookkeeping."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .business_documents import invoice, invoices
from .contact_store import ContactStore
from .document_store import DocumentStore
from .euer_store import BOOKING_DIRECTIONS, EUER_CATEGORIES, EuerStore, invoice_payment_key, invoice_payment_source


bp = Blueprint("euer", __name__, url_prefix="/documents/business/bookkeeping")


def _root() -> Path:
    return Path(current_app.config["DOCUMENT_ROOT"]).expanduser().resolve()


def _actor() -> str:
    return str(g.user["username"])


def _year() -> int:
    try:
        selected = int(request.args.get("year", date.today().year))
    except ValueError:
        selected = date.today().year
    return min(2200, max(1900, selected))


def _is_admin() -> bool:
    try:
        return bool(g.user["is_admin"])
    except (KeyError, TypeError, IndexError):
        return False


def _document_or_404(document_id: str) -> dict[str, Any]:
    try:
        return DocumentStore(_root()).get_document(document_id)
    except ValueError:
        abort(404)


@bp.get("")
@login_required
def overview():
    root, actor, year = _root(), _actor(), _year()
    store = EuerStore(root)
    admin = _is_admin()
    rows = store.bookings(year, actor=actor, is_admin=admin, include_reversed=True)
    selected_direction = request.args.get("direction", "").strip()
    query = request.args.get("q", "").strip()
    if selected_direction in BOOKING_DIRECTIONS:
        rows = [row for row in rows if row.get("direction") == selected_direction]
    else:
        selected_direction = ""
    if query:
        needle = query.casefold()
        rows = [row for row in rows if needle in " ".join(str(row.get(key, "")) for key in ("description", "category_label", "reference", "document_id", "invoice_id")).casefold()]

    documents: dict[str, dict[str, Any]] = {}
    document_store = DocumentStore(root)
    for document_id in {str(row.get("document_id", "")) for row in rows if row.get("document_id")}:
        try:
            documents[document_id] = document_store.get_document(document_id)
        except ValueError:
            documents[document_id] = {"document_id": document_id, "last_path": "Beleg nicht mehr vorhanden"}

    booked_sources = {
        str(row.get("source_id", ""))
        for row in store.bookings(actor=actor, is_admin=admin, include_reversed=False)
        if row.get("source_type") == "invoice_payment"
    }
    contacts = ContactStore(root)
    payment_candidates = []
    for invoice_row in invoices(root):
        contact_id = str(invoice_row.get("contact_id", ""))
        if not contacts.can_manage(contact_id, actor):
            continue
        for payment in invoice_row.get("payments", []):
            if not isinstance(payment, dict):
                continue
            if not str(payment.get("paid_at", "")).startswith(f"{year:04d}-") or str(invoice_row.get("currency", "EUR")).upper() != "EUR":
                continue
            source_id = invoice_payment_source(str(invoice_row["invoice_id"]), payment)
            if source_id not in booked_sources:
                payment_candidates.append(
                    {
                        "invoice": invoice_row,
                        "payment": payment,
                        "source_id": source_id,
                        "payment_key": invoice_payment_key(payment),
                    }
                )
    payment_candidates.sort(key=lambda item: str(item["payment"].get("paid_at", "")), reverse=True)

    return render_template(
        "documents/euer_overview.html",
        year=year,
        years=sorted(set(range(date.today().year + 1, date.today().year - 7, -1)) | {year}, reverse=True),
        rows=rows,
        summary=store.summary(year, actor=actor, is_admin=admin),
        settings=store.settings(),
        categories=EUER_CATEGORIES,
        documents=documents,
        payment_candidates=payment_candidates,
        query=query,
        selected_direction=selected_direction,
        is_admin=admin,
    )


@bp.get("/new")
@login_required
def new_booking():
    document_id = request.args.get("document_id", "").strip()
    document = _document_or_404(document_id) if document_id else None
    defaults = {
        "direction": request.args.get("direction", "expense"),
        "booking_date": date.today().isoformat(),
        "document_date": date.today().isoformat(),
        "description": document.get("last_path", "") if document else "",
        "category": "other_expense" if request.args.get("direction", "expense") != "income" else ("taxable_income" if EuerStore(_root()).settings()["vat_scheme"] == "standard" else "small_business_income"),
        "gross": "",
        "tax_mode": EuerStore(_root()).settings()["vat_scheme"],
        "tax_rate": "19",
        "business_share": "100",
    }
    return render_template("documents/euer_booking_form.html", values=defaults, document=document, categories=EUER_CATEGORIES)


@bp.post("/bookings")
@login_required
def create_booking():
    values = request.form.to_dict()
    document_id = values.get("document_id", "").strip()
    if document_id:
        _document_or_404(document_id)
    try:
        booking = EuerStore(_root()).add(values, _actor())
    except ValueError as exc:
        document = _document_or_404(document_id) if document_id else None
        flash(f"Buchung nicht gespeichert: {exc}")
        return render_template("documents/euer_booking_form.html", values=values, document=document, categories=EUER_CATEGORIES), 400
    flash("Beleg wurde gebucht und im Änderungsprotokoll festgehalten.")
    if request.form.get("return_to") == "document" and booking.get("document_id"):
        return redirect(url_for("documents.detail", document_id=booking["document_id"]))
    return redirect(url_for(".overview", year=booking["booking_date"][:4]))


@bp.post("/invoice-payments/<invoice_id>/<path:payment_key>")
@login_required
def book_invoice_payment(invoice_id: str, payment_key: str):
    root, actor = _root(), _actor()
    try:
        invoice_row = invoice(root, invoice_id)
    except ValueError:
        abort(404)
    if not ContactStore(root).can_manage(str(invoice_row.get("contact_id", "")), actor):
        abort(403)
    payment = next(
        (
            item
            for item in invoice_row.get("payments", [])
            if isinstance(item, dict) and invoice_payment_key(item) == payment_key
        ),
        None,
    )
    if payment is None:
        abort(404)
    try:
        booking = EuerStore(root).add_invoice_payment(invoice_row, payment, actor)
        flash(f"Zahlung zu Rechnung {invoice_row.get('invoice_number', invoice_id)} wurde gebucht.")
        return redirect(url_for(".overview", year=booking["booking_date"][:4]))
    except ValueError as exc:
        flash(f"Zahlung nicht gebucht: {exc}")
        return redirect(url_for(".overview", year=str(payment.get("paid_at", date.today().isoformat()))[:4]))


@bp.post("/bookings/<booking_id>/reverse")
@login_required
def reverse_booking(booking_id: str):
    try:
        booking = EuerStore(_root()).reverse(
            booking_id,
            request.form.get("reason", ""),
            _actor(),
            is_admin=_is_admin(),
        )
        flash("Buchung wurde storniert; der ursprüngliche Eintrag bleibt nachvollziehbar.")
        return redirect(url_for(".overview", year=booking["booking_date"][:4]))
    except PermissionError:
        abort(403)
    except ValueError as exc:
        flash(f"Stornierung nicht möglich: {exc}")
        return redirect(url_for(".overview", year=_year()))


@bp.post("/settings")
@login_required
def update_settings():
    if not _is_admin():
        abort(403)
    try:
        EuerStore(_root()).save_settings(request.form.to_dict(), _actor())
        flash("Umsatzsteuer-Einstellung gespeichert.")
    except ValueError as exc:
        flash(f"Einstellung nicht gespeichert: {exc}")
    return redirect(url_for(".overview", year=request.form.get("year", date.today().year)))


@bp.get("/export.csv")
@login_required
def export_csv():
    year = _year()
    payload = EuerStore(_root()).csv_export(year, actor=_actor(), is_admin=_is_admin())
    response = Response(payload, mimetype="text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = f'attachment; filename="EÜR-{year}.csv"'
    return response
