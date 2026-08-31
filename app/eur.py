"""Web UI for the intentionally small EÜR receipt workflow."""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for

from .auth import login_required
from .document_store import DocumentStore
from .eur_store import EurReceiptStore


bp = Blueprint("eur", __name__, url_prefix="/documents/accounting")


def _root() -> Path:
    return Path(current_app.config["DOCUMENT_ROOT"]).expanduser().resolve()


def _actor() -> str:
    return str(g.user["username"])


def _is_admin() -> bool:
    try: return bool(g.user["is_admin"])
    except (KeyError, TypeError, IndexError): return False


def _year() -> int:
    try: value = int(request.values.get("year", date.today().year))
    except (TypeError, ValueError): value = date.today().year
    return min(max(value, 2000), 2100)


@bp.get("/receipts")
@login_required
def receipts():
    year = _year(); store = EurReceiptStore(_root())
    rows = store.list(_actor(), is_admin=_is_admin(), year=year)
    return render_template("documents/eur_receipts.html", rows=rows, summary=store.summary(rows), year=year, today=date.today().isoformat())


@bp.post("/receipts")
@login_required
def create_receipt():
    upload = request.files.get("receipt_file")
    if upload is None or not upload.filename:
        flash("Bitte den Originalbeleg als Datei auswählen.")
        return redirect(url_for("eur.receipts", year=_year()))
    try:
        document = DocumentStore(_root()).import_upload(upload, upload.filename, _actor(), archive=True, max_bytes=int(current_app.config["MAX_CONTENT_LENGTH"]))
        receipt = EurReceiptStore(_root()).create(request.form, document, _actor())
        flash("Beleg gespeichert." if receipt["complete"] else "Beleg gespeichert; die Prüfung zeigt noch fehlende Angaben.")
    except (OSError, ValueError) as exc:
        flash(f"Beleg konnte nicht gespeichert werden: {exc}")
    return redirect(url_for("eur.receipts", year=_year()))


@bp.post("/receipts/<receipt_id>/review")
@login_required
def review_receipt(receipt_id: str):
    try:
        EurReceiptStore(_root()).set_reviewed(receipt_id, request.form.get("reviewed") == "1", _actor(), is_admin=_is_admin())
        flash("Prüfstatus aktualisiert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("eur.receipts", year=_year()))


@bp.post("/receipts/<receipt_id>/update")
@login_required
def update_receipt(receipt_id: str):
    try:
        EurReceiptStore(_root()).update(receipt_id, request.form, _actor(), is_admin=_is_admin())
        flash("Belegdaten aktualisiert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("eur.receipts", year=_year()))


@bp.get("/receipts/export.csv")
@login_required
def export_csv():
    year = _year(); store = EurReceiptStore(_root()); rows = store.list(_actor(), is_admin=_is_admin(), year=year)
    return send_file(io.BytesIO(store.csv_bytes(rows)), as_attachment=True, download_name=f"EÜR-Belege-{year}.csv", mimetype="text/csv; charset=utf-8")


@bp.get("/receipts/export.zip")
@login_required
def export_zip():
    year = _year(); root = _root(); store = EurReceiptStore(root); rows = store.list(_actor(), is_admin=_is_admin(), year=year)
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"EÜR-Belege-{year}.csv", store.csv_bytes(rows))
        for row in rows:
            try: document = store.documents.get_document(row["document_id"])
            except ValueError: continue
            path = root / str(document.get("last_path", ""))
            try: safe = path.resolve().is_relative_to(root)
            except AttributeError: safe = root == path.resolve() or root in path.resolve().parents
            if safe and path.is_file() and not path.is_symlink():
                archive.write(path, f"Belege/{row['receipt_date']}_{row['receipt_id'][:8]}_{row['document_name']}")
    target.seek(0)
    store.documents.history.record("eur_export_created", _actor(), "eur-export", str(year), {"year": year, "receipt_count": len(rows), "format": "zip"})
    return send_file(target, as_attachment=True, download_name=f"EÜR-Steuerberater-{year}.zip", mimetype="application/zip")


@bp.get("/receipts/<receipt_id>/document")
@login_required
def receipt_document(receipt_id: str):
    store = EurReceiptStore(_root())
    try: row = store.get(receipt_id, _actor(), is_admin=_is_admin()); document = store.documents.get_document(row["document_id"])
    except ValueError: abort(404)
    path = _root() / str(document.get("last_path", ""))
    if not path.is_file() or path.is_symlink(): abort(404)
    return send_file(path, as_attachment=True, download_name=row["document_name"])
