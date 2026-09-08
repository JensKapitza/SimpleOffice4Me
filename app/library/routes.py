"""Library workflow UI: shelf labels, bulk book assignment and label printing."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from flask import Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from ..auth import login_required
from ..object_store import ObjectStore
from .printer import (
    LABELS,
    MODELS,
    PrinterError,
    cut_feed,
    discover_printers,
    preview_png,
    print_image,
    render_barcode,
    render_image,
    render_text,
)
from .store import LibraryStore

bp = Blueprint("library", __name__, url_prefix="/library")
logger = logging.getLogger(__name__)


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


def _books_with_assignments(query: str = "", limit: int = 120) -> list[dict[str, Any]]:
    needle = str(query or "").strip().casefold()
    library = _store()
    locations = {row["location_id"]: row for row in library.locations()}
    rows: list[dict[str, Any]] = []
    for item in reversed(_objects().objects()):
        tags = {str(tag).casefold() for tag in item.get("tags", [])}
        if str(item.get("type", "")).casefold() not in {"book", "buch"} and "buch" not in tags and "isbn" not in tags:
            continue
        assignment = library.assignment(item["object_id"])
        location = locations.get(str((assignment or {}).get("location_id", "")), {})
        location_path = library.location_path(str(location.get("location_id", ""))) if location else str(item.get("location", ""))
        haystack = " ".join((
            str(item.get("display_id", "")), str(item.get("name", "")), str(item.get("identifier", "")),
            str(item.get("fields", {}).get("authors", "")), location_path, str(location.get("code", "")),
        )).casefold()
        if needle and needle not in haystack:
            continue
        rows.append({**item, "library_assignment": assignment or {}, "library_location": location, "library_location_path": location_path})
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
        printer=_store().printer_settings(),
        models=sorted(MODELS),
        labels=sorted(LABELS, key=lambda value: (len(value), value)),
        books=_books_with_assignments(request.args.get("q", ""), 80),
        events=_store().recent_events(25),
        discovered=discover_printers(),
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
    except (OSError, ValueError) as exc:
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
        flash(f"Etikett konnte nicht gedruckt werden: {exc}")
    return redirect(url_for("library.index", location=location_id) + "#locations")


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
        return render_barcode(request.form.get("value", ""), settings, caption=request.form.get("caption", ""))
    if kind == "image":
        upload = request.files.get("image")
        return render_image(upload.read(12 * 1024 * 1024 + 1) if upload else b"", settings)
    raise PrinterError("Unbekannter Drucktyp")


@bp.post("/printer/preview/<kind>")
@login_required
def printer_preview(kind: str):
    try:
        data = preview_png(_render_requested(kind))
    except (OSError, PrinterError, ValueError) as exc:
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
        flash(f"Drucken fehlgeschlagen: {exc}")
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
        flash(f"Vorschub/Schnitt fehlgeschlagen: {exc}")
    return redirect(url_for("library.index") + "#printer")
