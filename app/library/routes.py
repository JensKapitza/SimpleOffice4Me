"""Library workflow UI: shelf labels, bulk book assignment and label printing."""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from functools import wraps
from tools.service_control import exclusive_lease
from typing import Any
from urllib.parse import urlencode

from flask import Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template, request, send_file, url_for

from ..auth import login_required
from ..access_control import audit, is_admin
from ..object_store import ObjectStore
from ..document_store import CONTROL_DIR
from .printer import (
    LABELS,
    MODELS,
    PrinterError,
    cut_feed,
    discover_printers,
    preview_png,
    print_image,
    render_barcode,
    render_qr_code,
    render_image,
    render_text,
)
from .catalog import create_physical_copy
from .imports import bibtex_import_rows, calibre_import_rows, calibre_isbn, ris_import_rows
from .label_pdf import book_labels_pdf
from .store import LibraryStore

bp = Blueprint("library", __name__, url_prefix="/library")
logger = logging.getLogger(__name__)


@bp.after_request
def protect_export_and_ocr_responses(response):
    if request.endpoint in {
        "library.export_books_csv", "library.export_books_ris", "library.export_books_bibtex",
        "library.export_books_calibre",
        "library.optimize_ocr_image", "library.analyze_ocr_upload", "library.print_book_labels",
        "library.export_book_labels_pdf", "library.import_books_calibre",
        "library.import_books_ris", "library.import_books_bibtex",
        "library.create_book_copy",
    }:
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _root() -> Path:
    return Path(current_app.config["DOCUMENT_ROOT"])


def _store() -> LibraryStore:
    return LibraryStore(_root())


def _objects() -> ObjectStore:
    return ObjectStore(_root())


def _actor() -> str:
    return str(g.user["username"])


def _book_item(value: str) -> dict[str, Any] | None:
    code = str(value or "").strip()
    if not code:
        return None
    store = _objects()
    try:
        if re.fullmatch(r"[0-9a-f-]{36}", code, re.IGNORECASE):
            return store.object(code)
        if code.startswith("#") or code.isdigit():
            return store.object(code)
    except ValueError:
        pass
    candidates = store.objects(code)
    folded = code.casefold()
    for item in candidates:
        fields = item.get("fields", {}) if isinstance(item.get("fields"), dict) else {}
        exact = {
            str(item.get("identifier", "")).strip().casefold(),
            str(item.get("display_id", "")).strip().casefold(),
            str(item.get("original_display_id", "")).strip().casefold(),
            str(fields.get("isbn", "")).strip().casefold(),
            str(fields.get("barcode", "")).strip().casefold(),
        }
        if folded in exact:
            return item
    return None


def _object_update_values(item: dict[str, Any], *, location: str, location_id: str, location_code: str) -> dict[str, Any]:
    fields = dict(item.get("fields", {})) if isinstance(item.get("fields"), dict) else {}
    fields["library_location_id"] = location_id
    fields["library_location_code"] = location_code
    return {
        "name": item.get("name", ""),
        "type": item.get("type", "object"),
        "status": item.get("status", "active"),
        "description": item.get("description", ""),
        "identifier": item.get("identifier", ""),
        "location": location,
        "expires_at": item.get("expires_at", ""),
        "tags": item.get("tags", []),
        "fields": fields,
    }


def assign_object_to_location(object_id: str, location_id: str, actor: str, *, scanned: str = "") -> dict[str, Any]:
    """Assign an ObjectStore item and mirror the structured shelf into searchable fields."""
    objects = _objects()
    item = objects.object(object_id)
    library = _store()
    location = library.location(location_id)
    path = library.location_path(location_id) or str(location.get("name", ""))
    previous = _object_update_values(
        item,
        location=str(item.get("location", "")),
        location_id=str(item.get("fields", {}).get("library_location_id", "")),
        location_code=str(item.get("fields", {}).get("library_location_code", "")),
    )
    updated = objects.update(
        item["object_id"],
        _object_update_values(item, location=path, location_id=location_id, location_code=str(location["code"])),
        actor,
    )
    try:
        assignment = library.assign(item["object_id"], location_id, actor, scanned=scanned)
    except Exception:
        try:
            objects.update(item["object_id"], previous, actor)
        except Exception:
            logger.exception("library assignment rollback failed object=%s", item["object_id"])
        raise
    logger.info(
        "library_assignment actor=%s object=%s display_id=%s location=%s code=%s",
        actor, item["object_id"], item.get("display_id", ""), location_id, location.get("code", ""),
    )
    return {"item": updated, "location": location, "assignment": assignment, "location_path": path}


def _books_with_assignments(
    query: str = "",
    limit: int = 120,
    *,
    author: str = "",
    genre: str = "",
    location_id: str = "",
) -> list[dict[str, Any]]:
    needle = str(query or "").strip().casefold()
    author_needle = str(author or "").strip().casefold()
    genre_needle = str(genre or "").strip().casefold()
    selected_location = str(location_id or "").strip()
    library = _store()
    locations = {row["location_id"]: row for row in library.locations()}
    rows: list[dict[str, Any]] = []
    for item in reversed(_objects().objects()):
        tags = {str(tag).casefold() for tag in item.get("tags", [])}
        if str(item.get("type", "")).casefold() not in {"book", "buch"} and "buch" not in tags and "isbn" not in tags:
            continue
        assignment = library.assignment(item["object_id"])
        assigned_location_id = str((assignment or {}).get("location_id", ""))
        if selected_location and assigned_location_id != selected_location:
            continue
        location = locations.get(assigned_location_id, {})
        location_path = library.location_path(assigned_location_id) if location else str(item.get("location", ""))
        fields = item.get("fields", {})
        authors = str(fields.get("authors", fields.get("author", "")))
        genres = str(fields.get("genre", fields.get("genres", "")))
        if author_needle and author_needle not in authors.casefold():
            continue
        if genre_needle and genre_needle not in genres.casefold():
            continue
        haystack = " ".join((
            str(item.get("display_id", "")), str(item.get("name", "")), str(item.get("identifier", "")),
            authors, genres, location_path, str(location.get("code", "")),
        )).casefold()
        if needle and needle not in haystack:
            continue
        rows.append({
            **item,
            "library_assignment": assignment or {},
            "library_location": location,
            "library_location_path": location_path,
        })
        if len(rows) >= max(1, min(int(limit), 500)):
            break
    return rows


@bp.get("")
@login_required
def index():
    selected_location = request.args.get("location", "").strip()
    locations = _store().locations()
    if selected_location and not any(row["location_id"] == selected_location for row in locations):
        selected_location = ""
    return render_template(
        "library/index.html",
        locations=locations,
        selected_location=selected_location,
        author_filter=request.args.get("author", "").strip(),
        genre_filter=request.args.get("genre", "").strip(),
        printer=_store().printer_settings(),
        models=sorted(MODELS),
        labels=sorted(LABELS, key=lambda value: (len(value), value)),
        books=_books_with_assignments(
            request.args.get("q", ""), 80,
            author=request.args.get("author", ""),
            genre=request.args.get("genre", ""),
            location_id=selected_location,
        ),
        events=_store().recent_events(25),
        discovered=discover_printers(),
        ocr_available=bool(shutil.which("tesseract")),
        pdf_available=bool(shutil.which("pdftoppm")),
    )


@bp.post("/locations")
@login_required
def create_location():
    try:
        row = _store().create_location(
            request.form.get("name", ""), _actor(),
            parent_id=request.form.get("parent_id", ""), code=request.form.get("code", ""),
        )
        flash(f"Standort {row['name']} ({row['code']}) wurde angelegt.")
        return redirect(url_for("library.index", location=row["location_id"]) + "#bulk-scan")
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("library.index") + "#locations")


@bp.get("/locations/resolve")
@login_required
def resolve_location():
    row = _store().find_location(request.args.get("code", ""))
    if row is None:
        return jsonify({"found": False}), 404
    return jsonify({
        "found": True,
        "location": row,
        "path": _store().location_path(row["location_id"]),
    })


@bp.post("/assign")
@login_required
def assign_book():
    code = request.form.get("book_code", "").strip()
    location_id = request.form.get("location_id", "").strip()
    item = _book_item(code)
    if item is None:
        query = urlencode({
            "barcode": code,
            "book": "1",
            "library_location": location_id,
            "library_return": "1",
        })
        return jsonify({
            "ok": False,
            "not_found": True,
            "error": "Buch ist noch nicht im Inventar.",
            "capture_url": url_for("inventory.index") + "?" + query,
        }), 404
    try:
        result = assign_object_to_location(item["object_id"], location_id, _actor(), scanned=code)
    except (OSError, ValueError):
        return jsonify({"ok": False, "error": "library_request_rejected"}), 400
    updated = result["item"]
    return jsonify({
        "ok": True,
        "object_id": updated["object_id"],
        "display_id": updated.get("display_id", ""),
        "name": updated.get("name", ""),
        "location": result["location"],
        "location_path": result["location_path"],
        "detail_url": url_for("inventory.item_detail", object_id=updated["object_id"]),
    })


@bp.get("/find")
@login_required
def find_book():
    query = request.args.get("q", "").strip()
    exact = _book_item(query)
    if exact:
        assignment = _store().assignment(exact["object_id"])
        path = _store().location_path(str((assignment or {}).get("location_id", ""))) if assignment else str(exact.get("location", ""))
        return jsonify({
            "found": True,
            "item": {"object_id": exact["object_id"], "display_id": exact.get("display_id", ""), "name": exact.get("name", ""), "identifier": exact.get("identifier", "")},
            "assignment": assignment or {},
            "location_path": path,
            "detail_url": url_for("inventory.item_detail", object_id=exact["object_id"]),
        })
    rows = _books_with_assignments(query, 20)
    return jsonify({
        "found": bool(rows),
        "results": [
            {
                "object_id": row["object_id"], "display_id": row.get("display_id", ""), "name": row.get("name", ""),
                "identifier": row.get("identifier", ""), "location_path": row.get("library_location_path", ""),
                "location_code": row.get("library_location", {}).get("code", ""),
                "detail_url": url_for("inventory.item_detail", object_id=row["object_id"]),
            }
            for row in rows
        ],
    })


@bp.post("/books/<object_id>/copies")
@login_required
def create_book_copy(object_id: str):
    """Create one separately trackable physical copy of an existing book."""
    try:
        source, created = create_physical_copy(_objects(), object_id, _actor())
    except ValueError:
        audit(
            "library_book_copy_created", "library", "books", outcome="failure",
            detail={"reason": "invalid_source"},
        )
        message = "Exemplar konnte nicht angelegt werden. Bitte Ausgangsbuch prüfen."
        if request.accept_mimetypes.best == "application/json":
            return jsonify({"ok": False, "error": message}), 400
        flash(message)
        return redirect(url_for("library.index"))
    audit(
        "library_book_copy_created", "library", created["object_id"], outcome="success",
        detail={"source_object_id": source["object_id"]},
    )
    if request.accept_mimetypes.best == "application/json":
        return jsonify({
            "ok": True,
            "source_object_id": source["object_id"],
            "object_id": created["object_id"],
            "display_id": created["display_id"],
        }), 201
    flash(f"Weiteres Exemplar #{created['display_id']} wurde ohne Standort angelegt.")
    return redirect(url_for("library.index"))


@bp.post("/locations/<location_id>/print")
@login_required
def print_location(location_id: str):
    library = _store()
    try:
        location = library.location(location_id)
        settings = library.printer_settings()
        image = render_barcode(str(location["code"]), settings, caption=library.location_path(location_id))
        byte_count = print_image(image, settings)
        library.record_print(_actor(), kind="location", status="printed", summary=f"{location['code']} {location['name']}", byte_count=byte_count)
        flash(f"Regaletikett {location['code']} wurde gedruckt.")
    except (OSError, PrinterError, ValueError) as exc:
        library.record_print(_actor(), kind="location", status="failed", summary=location_id, error=str(exc))
        flash("Etikett konnte nicht gedruckt werden. Bitte Druckereinstellungen prüfen.")
    return redirect(url_for("library.index", location=location_id) + "#locations")


def _book_label_data(item: dict[str, Any]) -> tuple[str, str]:
    """Return a stable machine value and readable caption for a book label."""
    fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
    value = (
        fields.get("barcode") or fields.get("isbn") or item.get("identifier")
        or item.get("display_id") or item.get("object_id") or ""
    )
    authors = str(fields.get("authors") or "").strip()
    caption = " · ".join(part for part in (str(item.get("name") or "").strip(), authors) if part)
    return str(value).strip()[:120], caption[:240]


def _selected_book_label_items(
    identifiers: list[str],
) -> tuple[list[tuple[dict[str, Any], str, str]] | None, tuple[Response, int] | None]:
    if not identifiers or len(identifiers) > 100:
        return None, (jsonify({"ok": False, "error": "Bitte 1 bis 100 Bücher auswählen."}), 400)
    if any(not identifier or len(identifier) > 80 for identifier in identifiers):
        return None, (jsonify({"ok": False, "error": "Ungültige Buchauswahl."}), 400)

    objects = _objects()
    items = []
    seen = set()
    for identifier in identifiers:
        try:
            item = objects.object(identifier)
        except (ValueError, OSError):
            return None, (jsonify({"ok": False, "error": "Mindestens ein ausgewähltes Buch wurde nicht gefunden."}), 404)
        tags = {str(tag).casefold() for tag in item.get("tags", [])}
        if str(item.get("type", "")).casefold() not in {"book", "buch"} and not tags.intersection({"buch", "isbn"}):
            return None, (jsonify({"ok": False, "error": "Die Auswahl enthält einen Nicht-Buch-Datensatz."}), 400)
        if item["object_id"] in seen:
            continue
        seen.add(item["object_id"])
        value, caption = _book_label_data(item)
        if not value:
            return None, (jsonify({"ok": False, "error": "Für mindestens ein Buch fehlt eine druckbare Kennung."}), 400)
        items.append((item, value, caption))
    return items, None


@bp.post("/labels/books/print")
@login_required
def print_book_labels():
    identifiers = request.form.getlist("object_id")
    style = request.form.get("barcode_style", "with_text")
    if style not in {"with_text", "barcode_only", "qr_with_text", "qr_only"}:
        return jsonify({"ok": False, "error": "Bitte 1 bis 100 Bücher und eine gültige Etikettenart auswählen."}), 400
    items, error = _selected_book_label_items(identifiers)
    if error:
        return error

    library = _store()
    settings = library.printer_settings()
    printed = 0
    for item, value, caption in items:
        try:
            qr_style = style.startswith("qr_")
            image = (render_qr_code if qr_style else render_barcode)(
                str(item["object_id"]) if qr_style else value,
                settings,
                caption=caption if style in {"with_text", "qr_with_text"} else "",
                show_text=style in {"with_text", "qr_with_text"},
            )
            byte_count = print_image(image, settings)
        except (OSError, PrinterError, ValueError) as exc:
            library.record_print(
                _actor(), kind="book-label", status="failed",
                summary=str(item.get("display_id") or item["object_id"]),
                error=str(exc),
            )
            return jsonify({
                "ok": False,
                "printed": printed,
                "total": len(items),
                "error": "Druck fehlgeschlagen; bereits gedruckte Etiketten bleiben ausgegeben.",
            }), 502
        library.record_print(
            _actor(), kind="book-label", status="printed",
            summary=str(item.get("display_id") or item["object_id"]),
            byte_count=byte_count,
        )
        printed += 1
    return jsonify({"ok": True, "printed": printed, "total": len(items)})


@bp.post("/labels/books.pdf")
@login_required
def export_book_labels_pdf():
    identifiers = request.form.getlist("object_id")
    style = request.form.get("barcode_style", "with_text")
    layout = request.form.get("label_layout", "media")
    if style not in {"with_text", "barcode_only", "qr_with_text", "qr_only"}:
        return jsonify({"ok": False, "error": "Bitte 1 bis 100 Bücher und eine gültige Etikettenart auswählen."}), 400
    if layout not in {"media", "a4_3x7"}:
        return jsonify({"ok": False, "error": "Bitte ein gültiges Etikettenlayout auswählen."}), 400
    items, error = _selected_book_label_items(identifiers)
    if error:
        return error
    qr_style = style.startswith("qr_")
    labels = [
        (str(item["object_id"]) if qr_style else value, caption)
        for item, value, caption in items
    ]

    try:
        payload = book_labels_pdf(labels, _store().printer_settings(), style=style, layout=layout)
    except (OSError, PrinterError, ValueError):
        return jsonify({"ok": False, "error": "PDF-Etiketten konnten mit der gewählten Größe nicht erstellt werden."}), 400
    return send_file(
        io.BytesIO(payload),
        mimetype="application/pdf",
        as_attachment=True,
        download_name="buch-etiketten.pdf",
        max_age=0,
    )


@bp.post("/labels/books.preview")
@login_required
def preview_book_labels():
    identifiers = request.form.getlist("object_id")
    style = request.form.get("barcode_style", "with_text")
    if style not in {"with_text", "barcode_only", "qr_with_text", "qr_only"}:
        return jsonify({"ok": False, "error": "Ungültige Etikettenart."}), 400
    items, error = _selected_book_label_items(identifiers)
    if error:
        return error
    item, value, caption = items[0]
    qr_style = style.startswith("qr_")
    try:
        image = (render_qr_code if qr_style else render_barcode)(
            str(item["object_id"]) if qr_style else value,
            _store().printer_settings(),
            caption=caption if style in {"with_text", "qr_with_text"} else "",
            show_text=style in {"with_text", "qr_with_text"},
        )
        data = preview_png(image)
    except (OSError, PrinterError, ValueError):
        return jsonify({"ok": False, "error": "Etikettenvorschau konnte nicht erstellt werden."}), 400
    return Response(data, mimetype="image/png", headers={"Cache-Control": "no-store"})


@bp.post("/printer/settings")
@login_required
def printer_settings():
    values = {
        "uri": request.form.get("uri", ""),
        "model": request.form.get("model", ""),
        "label": request.form.get("label", ""),
        "font_name": request.form.get("font_name", ""),
        "font_size": request.form.get("font_size", ""),
        "color": request.form.get("color", "black"),
        "align": request.form.get("align", "center"),
        "bold": request.form.get("bold") == "1",
        "cut": request.form.get("cut") == "1",
    }
    if values["model"].upper() not in MODELS:
        flash("Unbekanntes Brother-Druckermodell.")
        return redirect(url_for("library.index") + "#printer")
    if values["label"].lower() not in LABELS:
        flash("Unbekannte Etikettengröße.")
        return redirect(url_for("library.index") + "#printer")
    try:
        _store().update_printer_settings(values, _actor())
        flash("Druckeinstellungen gespeichert und werden für die nächsten Drucke weiterverwendet.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("library.index") + "#printer")


@bp.get("/printer/discover")
@login_required
def printer_discover():
    return jsonify({"printers": discover_printers()})


def _render_requested(kind: str):
    settings = _store().printer_settings()
    if kind == "text":
        return render_text(request.form.get("text", ""), settings)
    if kind == "barcode":
        return render_barcode(request.form.get("value", ""), settings, caption=request.form.get("caption", ""),
                              show_text=request.form.get("barcode_style", "with_text") == "with_text")
    if kind == "image":
        upload = request.files.get("image")
        return render_image(upload.read(12 * 1024 * 1024 + 1) if upload else b"", settings)
    raise PrinterError("Unbekannter Drucktyp")


@bp.post("/printer/preview/<kind>")
@login_required
def printer_preview(kind: str):
    try:
        data = preview_png(_render_requested(kind))
    except (OSError, PrinterError, ValueError):
        return jsonify({"ok": False, "error": "library_request_rejected"}), 400
    return Response(data, mimetype="image/png", headers={"Cache-Control": "no-store"})


@bp.post("/printer/print/<kind>")
@login_required
def printer_print(kind: str):
    library = _store()
    try:
        image = _render_requested(kind)
        byte_count = print_image(image, library.printer_settings())
        summary = request.form.get("text", "") if kind == "text" else request.form.get("value", "") if kind == "barcode" else str(getattr(request.files.get("image"), "filename", "Bild"))
        library.record_print(_actor(), kind=kind, status="printed", summary=summary, byte_count=byte_count)
        flash("Etikett wurde an den Brother-Drucker gesendet.")
    except (OSError, PrinterError, ValueError) as exc:
        library.record_print(_actor(), kind=kind, status="failed", summary=kind, error=str(exc))
        flash("Drucken fehlgeschlagen. Bitte Drucker und Eingaben prüfen.")
    return redirect(url_for("library.index") + "#printer")


@bp.post("/printer/cut")
@login_required
def printer_cut():
    library = _store()
    try:
        byte_count = cut_feed(library.printer_settings())
        library.record_print(_actor(), kind="cut", status="printed", summary="Vorschub und Schnitt", byte_count=byte_count)
        flash("Vorschub/Schnitt wurde an den Drucker gesendet.")
    except (OSError, PrinterError, ValueError) as exc:
        library.record_print(_actor(), kind="cut", status="failed", summary="Vorschub und Schnitt", error=str(exc))
        flash("Vorschub/Schnitt fehlgeschlagen. Bitte Drucker prüfen.")
    return redirect(url_for("library.index") + "#printer")


def _csv_cell(value):
    # NUL is not useful spreadsheet text and breaks the supported Python 3.10 CSV writer.
    text = "" if value is None else str(value).replace("\x00", "")
    index = 0
    while index < len(text) and (ord(text[index]) <= 32 or text[index].isspace() or text[index] == "\ufeff"):
        index += 1
    return "'" + text if text[index:].startswith(("=", "+", "-", "@")) else text


def _library_export_selection():
    """Authorize once and keep every export tied to the configured local root."""
    if not is_admin():
        audit("library_books_export", "library", "books", outcome="failure", detail={"reason": "administrator_required"})
        abort(403)
    objects = _objects()
    placements = _store().export_placements()
    identifiers = request.args.getlist("object_id")
    if len(identifiers) > 500:
        abort(400)
    filters_active = any(
        request.args.get(name, "").strip()
        for name in ("q", "author", "genre", "location")
    )
    selected = {}
    for identifier in identifiers:
        if not identifier or len(identifier) > 80:
            abort(404)
        try:
            item = objects.object(identifier)
        except ValueError:
            abort(404)
        tags = {str(tag).casefold() for tag in item.get("tags", [])}
        if str(item.get("type", "")).casefold() not in {"book", "buch"} and not tags.intersection({"buch", "isbn"}):
            abort(404)
        selected[item["object_id"]] = item
    if not identifiers and filters_active:
        filtered = _books_with_assignments(
            request.args.get("q", ""),
            500,
            author=request.args.get("author", ""),
            genre=request.args.get("genre", ""),
            location_id=request.args.get("location", ""),
        )
        selected = {item["object_id"]: item for item in filtered}
        identifiers = list(selected)
    return objects, placements, identifiers, selected, filters_active


def _library_export_books(objects, identifiers, selected, *, filters_active=False):
    source = selected.values() if identifiers or filters_active else objects.iter_objects()
    for item in source:
        tags = {str(tag).casefold() for tag in item.get("tags", [])}
        if str(item.get("type", "")).casefold() in {"book", "buch"} or tags.intersection({"buch", "isbn"}):
            yield item


def _record_library_export(export_format: str, count: int, completed: bool):
    audit("library_books_export", "library", "books",
          outcome="success" if completed else "failure",
          detail={"format": export_format, "rows": count, "complete": completed})


def _export_bibliography(export_format: str):
    from flask import stream_with_context

    from .bibliography import bibtex_record, ris_record

    objects, placements, identifiers, selected, filters_active = _library_export_selection()
    serializer = ris_record if export_format == "ris" else bibtex_record
    filename = "library-books.ris" if export_format == "ris" else "library-books.bib"
    mimetype = "application/x-research-info-systems" if export_format == "ris" else "application/x-bibtex"

    def generate():
        count = 0
        completed = False
        try:
            yield "\ufeff"
            for item in _library_export_books(objects, identifiers, selected, filters_active=filters_active):
                yield serializer(item, placements.get(item["object_id"], {}))
                count += 1
            completed = True
        finally:
            _record_library_export(export_format, count, completed)

    return Response(stream_with_context(generate()), mimetype=mimetype, headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    })


@bp.get("/export/books.csv")
@login_required
def export_books_csv():
    """Stream CSV rows to avoid accumulating the entire export in memory."""
    from flask import stream_with_context

    # Capture request-scoped storage before the response starts streaming.
    objects, placements, identifiers, selected, filters_active = _library_export_selection()

    def generate():
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        count = 0
        completed = False
        try:
            yield "\ufeff"
            writer.writerow(("ID", "Titel", "ISBN", "Autoren", "Genre", "Reihe", "Band", "Standort", "Regalcode"))
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)
            for item in _library_export_books(objects, identifiers, selected, filters_active=filters_active):
                fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
                placement = placements.get(item["object_id"], {})
                values = (
                    item.get("display_id", ""), item.get("name", ""),
                    fields.get("isbn") or item.get("identifier", ""),
                    fields.get("authors", ""), fields.get("genre", ""),
                    fields.get("series", ""), fields.get("series_number", ""),
                    placement.get("path") or item.get("location", ""),
                    placement.get("code") or fields.get("library_location_code", ""),
                )
                # Escape spreadsheet formulas while preserving the full field value.
                writer.writerow([_csv_cell(value) for value in values])
                count += 1
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)

            completed = True
        finally:
            _record_library_export("csv", count, completed)

    return Response(stream_with_context(generate()), mimetype="text/csv", headers={
        "Content-Disposition": "attachment; filename=library-books.csv",
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@bp.get("/export/books.ris")
@login_required
def export_books_ris():
    return _export_bibliography("ris")


@bp.get("/export/books.bib")
@login_required
def export_books_bibtex():
    return _export_bibliography("bibtex")


@bp.get("/export/books.calibre.zip")
@login_required
def export_books_calibre():
    """Create a bounded-memory Calibre bundle from the canonical local book records."""
    from .bibliography import CALIBRE_CSV_HEADERS, calibre_csv_values, calibre_opf_record

    objects, placements, identifiers, selected, filters_active = _library_export_selection()
    archive_file = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
    # Python 3.10 SpooledTemporaryFile does not provide the complete buffered-I/O
    # interface required by TextIOWrapper. A disk-backed TemporaryFile keeps the
    # export bounded-memory while working consistently across supported runtimes.
    csv_file = tempfile.TemporaryFile(mode="w+b")
    count = 0
    try:
        with zipfile.ZipFile(archive_file, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            text_stream = io.TextIOWrapper(csv_file, encoding="utf-8-sig", newline="")
            try:
                writer = csv.writer(text_stream)
                writer.writerow(CALIBRE_CSV_HEADERS)
                for item in _library_export_books(objects, identifiers, selected, filters_active=filters_active):
                    placement = placements.get(item["object_id"], {})
                    writer.writerow([_csv_cell(value) for value in calibre_csv_values(item, placement)])
                    count += 1
                    object_id = str(item.get("object_id", ""))
                    stem = re.sub(r"[^A-Za-z0-9._-]", "_", object_id)[:48] or "book"
                    suffix = hashlib.sha256(object_id.encode("utf-8")).hexdigest()[:12]
                    archive.writestr(
                        f"opf/{stem}-{suffix}.opf",
                        calibre_opf_record(item, placement),
                    )
                text_stream.flush()
            finally:
                text_stream.detach()
            csv_file.seek(0)
            with archive.open("metadata.csv", "w") as csv_member:
                shutil.copyfileobj(csv_file, csv_member, length=64 * 1024)
        archive_file.seek(0)
        response = send_file(
            archive_file, mimetype="application/zip", as_attachment=True,
            download_name="library-books-calibre.zip",
        )
        response.call_on_close(archive_file.close)
        _record_library_export("calibre_csv_opf", count, True)
        return response
    except Exception:
        archive_file.close()
        _record_library_export("calibre_csv_opf", count, False)
        raise
    finally:
        csv_file.close()



def _create_imported_books(rows: list[dict[str, str]], import_format: str) -> dict[str, int]:
    objects = _objects()
    existing_isbns = {
        str(item.get("fields", {}).get("isbn", "")).strip().casefold()
        for item in objects.objects()
        if str(item.get("fields", {}).get("isbn", "")).strip()
    }
    created = 0
    skipped = 0
    for row in rows:
        isbn = str(row.get("isbn", "")).strip()[:64]
        if isbn and isbn.casefold() in existing_isbns:
            skipped += 1
            continue
        fields = {
            key: str(row.get(key, ""))
            for key in (
                "authors", "isbn", "genre", "series", "series_number", "publisher",
                "year", "language", "edition", "description", "bibliography_note",
            )
            if row.get(key)
        }
        objects.create({
            "name": row["title"], "type": "book", "identifier": isbn,
            "tags": ["buch", "isbn"] if isbn else ["buch"], "fields": fields,
        }, _actor())
        if isbn:
            existing_isbns.add(isbn.casefold())
        created += 1
    audit("library_books_import", "library", "books", outcome="success",
          detail={"format": import_format, "created": created, "skipped": skipped})
    return {"created": created, "skipped": skipped, "total": len(rows)}


@bp.post("/import/books.calibre")
@login_required
def import_books_calibre():
    if not is_admin():
        audit("library_books_import", "library", "books", outcome="failure", detail={"reason": "administrator_required"})
        abort(403)
    upload = request.files.get("file")
    try:
        rows = calibre_import_rows(upload.read(12 * 1024 * 1024 + 1) if upload else b"")
    except (OSError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    normalized = [{
        "title": row["title"], "authors": row["authors"].replace(" & ", "; "),
        "isbn": calibre_isbn(row["identifiers"]), "genre": row["tags"],
        "series": row["series"], "series_number": row["series_index"],
        "publisher": row["publisher"], "year": row["published"],
        "language": row["language"], "description": row["comments"],
    } for row in rows]
    result = _create_imported_books(normalized, "calibre_csv")
    return jsonify({"ok": True, **result})


@bp.post("/import/books.ris")
@login_required
def import_books_ris():
    if not is_admin():
        audit("library_books_import", "library", "books", outcome="failure",
              detail={"reason": "administrator_required"})
        abort(403)
    upload = request.files.get("file")
    try:
        rows = ris_import_rows(upload.read(4 * 1024 * 1024 + 1) if upload else b"")
    except (OSError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    result = _create_imported_books(rows, "ris")
    return jsonify({"ok": True, **result})


@bp.post("/import/books.bib")
@login_required
def import_books_bibtex():
    if not is_admin():
        audit("library_books_import", "library", "books", outcome="failure",
              detail={"reason": "administrator_required"})
        abort(403)
    upload = request.files.get("file")
    try:
        rows = bibtex_import_rows(upload.read(4 * 1024 * 1024 + 1) if upload else b"")
    except (OSError, ValueError) as exc:
        logger.info("BibTeX import rejected: %s", type(exc).__name__)
        return jsonify({"ok": False, "error": "BibTeX-Datei konnte nicht gelesen werden."}), 400
    result = _create_imported_books(rows, "bibtex")
    return jsonify({"ok": True, **result})


def _ocr_action(function):
    @wraps(function)
    def controlled():
        with exclusive_lease(_root() / CONTROL_DIR / "library-ocr.lock") as acquired:
            if not acquired:
                return jsonify({"error": "Eine Bild-/OCR-Verarbeitung läuft bereits. Bitte erneut versuchen."}), 503
            return function()
    return controlled


@bp.post("/ocr/optimize")
@login_required
@_ocr_action
def optimize_ocr_image():
    """Return a preview copy; never replace the source image implicitly."""
    from .image_preprocessing import MAX_UPLOAD
    from .ocr_input import optimize_upload
    upload = request.files.get("image")
    if upload is None:
        return jsonify({"error": "Bild fehlt"}), 400
    data = upload.read(MAX_UPLOAD + 1)
    try:
        page = int(request.form.get("page", "1")) - 1
        if page < 0:
            raise ValueError("Invalid page number")
        optimized, source_format = optimize_upload(data,
                                  page=page,
                                  brighten=request.form.get("brighten", "1") == "1",
                                  straighten=request.form.get("straighten", "1") == "1")
    except (ValueError, TypeError) as exc:
        logger.info("OCR input rejected: %s", type(exc).__name__)
        return jsonify({"error": "Dateiformat, Seitennummer oder Bildgröße ungültig"}), 400
    return Response(optimized, mimetype="image/png", headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@bp.post("/ocr/analyze")
@login_required
@_ocr_action
def analyze_ocr_upload():
    """Recognize text in a photo or selected PDF page using local Tesseract."""
    import tempfile
    from .image_preprocessing import MAX_UPLOAD
    from .ocr_input import optimize_upload
    from ..object_vision import _run_tesseract

    upload = request.files.get("image")
    if upload is None:
        return jsonify({"error": "Datei fehlt"}), 400
    data = upload.read(MAX_UPLOAD + 1)
    try:
        page = int(request.form.get("page", "1")) - 1
        if page < 0:
            raise ValueError("Ungültige PDF-Seite")
        optimized, source_format = optimize_upload(
            data, page=page,
            brighten=request.form.get("brighten", "1") == "1",
            straighten=request.form.get("straighten", "1") == "1",
        )
    except (ValueError, TypeError) as exc:
        logger.info("OCR image optimization rejected: %s", type(exc).__name__)
        return jsonify({"error": "Ungültiges Bild, Dateiformat oder PDF-Seite"}), 400
    with tempfile.TemporaryDirectory(prefix="library-ocr-") as directory:
        image_path = Path(directory) / "page.png"
        image_path.write_bytes(optimized)
        result = _run_tesseract(image_path, timeout=15)
    if result.get("status") != "completed":
        logger.warning("OCR failed with status %s", result.get("status"))
        return jsonify({"error": "OCR-Verarbeitung derzeit nicht verfügbar", "status": "failed"}), 503
    import re
    words = re.findall(r"[^\W_][\w-]{3,}", result.get("text", ""), flags=re.UNICODE)
    keywords = list(dict.fromkeys(word.casefold()[:48] for word in words if not word.isdigit()))[:20]
    return jsonify({"status": "completed", "source_format": source_format,
                    "page": page + 1, "text": result.get("text", ""),
                    "suggested_tags": keywords, "engine": "tesseract",
                    "persisted": False})
