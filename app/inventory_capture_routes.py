"""Capture-inbox routes on the shared inventory blueprint.

Thin HTTP layer only; analysis and storage live in
:mod:`app.inventory_capture` and :mod:`app.capture_inbox_store`. Imported
at the end of :mod:`app.inventory` so the shared blueprint object
already exists (no behavior change, same endpoint names).
"""

from __future__ import annotations

from typing import Any

from flask import (
    abort,
    current_app,
    g,
    jsonify,
    render_template,
    request,
    send_file,
    url_for,
)

from .auth import login_required
from .capture_inbox_store import CaptureInboxConflict, CaptureInboxIndexError
from .inventory import MAX_PHOTO_BYTES, _single_line, bp

INDEX_ERROR = (
    "Eingangskorb ist vorübergehend nicht verfügbar "
    "(Index beschädigt). Die gespeicherten Originale bleiben erhalten."
)


@bp.get("/capture")
@login_required
def capture_inbox():
    """Mobile-first photo inbox: persistent entries, then adopt or correct."""
    from .capture_inbox_store import CaptureInboxStore
    from .inventory_capture import public_entry

    actor = str(g.user["username"])
    root = current_app.config["DOCUMENT_ROOT"]
    inbox_error = ""
    try:
        stored_entries = CaptureInboxStore(root).list(actor)
    except CaptureInboxIndexError:
        stored_entries = []
        inbox_error = INDEX_ERROR
    entries = []
    for stored in stored_entries:
        item = public_entry(
            stored,
            url_for("inventory.capture_preview",
                    entry_id=stored["entry_id"]),
        )
        item["assign_url"] = url_for(
            "inventory.capture_assign", entry_id=stored["entry_id"])
        item["analyze_url"] = url_for(
            "inventory.capture_reanalyze", entry_id=stored["entry_id"])
        item["batch_url"] = url_for(
            "inventory.capture_move", entry_id=stored["entry_id"])
        entries.append(item)
    return render_template(
        "inventory/capture.html",
        analyze_url=url_for("inventory.capture_analyze"),
        upload_url=url_for("inventory.capture_upload"),
        books_url=url_for("inventory.create_book"),
        items_url=url_for("inventory.create_item"),
        lookup_url=url_for("inventory.book_lookup"),
        ml_status_url=url_for("contextflow.ml_status_route"),
        batch_url_pattern=url_for(
            "inventory.capture_batch", batch_id="0" * 32).replace(
            "0" * 32, "__BATCH__"),
        entries=entries,
        inbox_error=inbox_error,
    )


@bp.post("/capture/upload")
@login_required
def capture_upload():
    """Persist one photo immediately, then analyze the stored original."""
    from .capture_inbox_store import CaptureInboxStore
    from .inventory_capture import analyze_photo, public_entry, store_upload

    actor = str(g.user["username"])
    root = current_app.config["DOCUMENT_ROOT"]
    try:
        entry, duplicate, raw = store_upload(
            request.files.get("photo"),
            _single_line(request.form.get("client_request_id", ""), 64),
            actor,
            root,
            _single_line(request.form.get("batch_id", ""), 64),
        )
    except CaptureInboxIndexError:
        return jsonify({"ok": False, "error": INDEX_ERROR}), 503
    except CaptureInboxConflict as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    analysis_error = ""
    try:
        payload = analyze_photo(raw, entry["entry_id"])
    except ValueError as exc:
        payload = {}
        analysis_error = str(exc)
    if payload:
        store = CaptureInboxStore(root)
        try:
            updated = store.save_analysis(entry["entry_id"], actor, payload)
        except CaptureInboxIndexError:
            updated = None
            analysis_error = INDEX_ERROR
        if updated is not None:
            entry = updated
    item = public_entry(
        entry,
        url_for("inventory.capture_preview", entry_id=entry["entry_id"]),
    )
    item["assign_url"] = url_for(
        "inventory.capture_assign", entry_id=entry["entry_id"])
    item["analyze_url"] = url_for(
        "inventory.capture_reanalyze", entry_id=entry["entry_id"])
    item["batch_url"] = url_for(
        "inventory.capture_move", entry_id=entry["entry_id"])
    return jsonify({
        "ok": True,
        "duplicate": duplicate,
        "entry": item,
        "analysis_error": analysis_error,
    }), 200


@bp.get("/capture/<entry_id>/preview")
@login_required
def capture_preview(entry_id: str):
    """Serve the stored original to its owner (no-store, actor-scoped)."""
    from .capture_inbox_store import CaptureInboxStore

    try:
        path = CaptureInboxStore(
            current_app.config["DOCUMENT_ROOT"]
        ).media_path(entry_id, str(g.user["username"]))
    except CaptureInboxIndexError:
        abort(503, description=INDEX_ERROR)
    except ValueError:
        abort(404)
    mimetype = {
        ".jpg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "application/octet-stream")
    response = send_file(path, mimetype=mimetype, conditional=True)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/capture/<entry_id>/assign")
@login_required
def capture_assign(entry_id: str):
    """Link a stored capture to an existing book/object (idempotent)."""
    from .capture_inbox_store import CaptureInboxStore
    from .inventory_capture import assign_entry, public_entry

    actor = str(g.user["username"])
    root = current_app.config["DOCUMENT_ROOT"]
    try:
        known = CaptureInboxStore(root).get(entry_id, actor)
    except CaptureInboxIndexError:
        return jsonify({"ok": False, "error": INDEX_ERROR}), 503
    if known is None:
        abort(404)
    data = request.get_json(silent=True) or request.form
    try:
        updated, attached, notice = assign_entry(
            entry_id, actor,
            str(data.get("kind", "")),
            str(data.get("object_id", "")),
            root,
        )
    except CaptureInboxIndexError:
        return jsonify({"ok": False, "error": INDEX_ERROR}), 503
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({
        "ok": True,
        "attached": attached,
        "notices": [notice] if notice else [],
        "entry": public_entry(
            updated,
            url_for("inventory.capture_preview", entry_id=entry_id),
        ),
    }), 200


@bp.post("/capture/<entry_id>/analyze")
@login_required
def capture_reanalyze(entry_id: str):
    """Re-run analysis on the stored original (entry keeps its history)."""
    from .capture_inbox_store import CaptureInboxStore
    from .inventory_capture import public_entry, reanalyze_entry

    actor = str(g.user["username"])
    root = current_app.config["DOCUMENT_ROOT"]
    try:
        known = CaptureInboxStore(root).get(entry_id, actor)
    except CaptureInboxIndexError:
        return jsonify({"ok": False, "error": INDEX_ERROR}), 503
    if known is None:
        abort(404)
    try:
        updated = reanalyze_entry(entry_id, actor, root)
    except CaptureInboxIndexError:
        return jsonify({"ok": False, "error": INDEX_ERROR}), 503
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({
        "ok": True,
        "entry": public_entry(
            updated,
            url_for("inventory.capture_preview", entry_id=entry_id),
        ),
    }), 200


@bp.post("/capture/analyze")
@login_required
def capture_analyze():
    """Analyze one inbox photo: OCR, ISBN, ML label, routing, duplicates.

    Nothing is stored. ML runs only on real local backends; missing
    backends are reported per photo, never faked.
    """
    from .inventory_capture import analyze_photo

    upload = request.files.get("photo")
    photo_id = _single_line(request.form.get("photo_id", ""), 64)
    if upload is None or not photo_id:
        return jsonify({"ok": False, "error": "Foto und Foto-Kennung fehlen."}), 400
    try:
        raw = upload.stream.read(MAX_PHOTO_BYTES + 1)
    except OSError:
        return jsonify({"ok": False, "error": "Foto konnte nicht gelesen werden."}), 400
    if not raw or len(raw) > MAX_PHOTO_BYTES:
        return jsonify({"ok": False, "error": "Foto fehlt oder überschreitet 12 MiB."}), 400
    try:
        payload = analyze_photo(raw, photo_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify(payload), 200


def _public_batch_entries(
    stored_entries: list[dict], public_entry: Any,
) -> list[dict]:
    """Project one book group for JSON (capture order, no server paths)."""
    items = []
    for stored in stored_entries:
        entry_id = str(stored.get("entry_id", ""))
        item = public_entry(
            stored,
            url_for("inventory.capture_preview", entry_id=entry_id),
        )
        item["assign_url"] = url_for(
            "inventory.capture_assign", entry_id=entry_id)
        item["analyze_url"] = url_for(
            "inventory.capture_reanalyze", entry_id=entry_id)
        item["batch_url"] = url_for(
            "inventory.capture_move", entry_id=entry_id)
        items.append(item)
    return items


@bp.get("/capture/batch/<batch_id>")
@login_required
def capture_batch(batch_id: str):
    """Review one book group: photos in capture order + combined draft."""
    from .capture_inbox_store import CaptureInboxStore
    from .inventory_capture import public_entry, summarize_batch

    actor = str(g.user["username"])
    root = current_app.config["DOCUMENT_ROOT"]
    try:
        stored_entries = CaptureInboxStore(root).list_batch(actor, batch_id)
    except CaptureInboxIndexError:
        return jsonify({"ok": False, "error": INDEX_ERROR}), 503
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not stored_entries:
        abort(404)
    clean_batch = str(batch_id or "").strip().lower()
    return jsonify({
        "ok": True,
        "batch_id": clean_batch,
        "entries": _public_batch_entries(stored_entries, public_entry),
        "combined": summarize_batch(stored_entries),
        "analyze_url": url_for(
            "inventory.capture_batch_analyze", batch_id=clean_batch),
        "adopt_url": url_for(
            "inventory.capture_batch_adopt", batch_id=clean_batch),
    }), 200


@bp.post("/capture/batch/<batch_id>/analyze")
@login_required
def capture_batch_analyze(batch_id: str):
    """Analyze every stored original of one book group together.

    The optional ``consent_meta`` field is an explicit per-request
    opt-in for the Meta cloud path (unchecked by default); without
    it only real local backends answer. Nothing is invented and
    nothing is deleted: per-photo failures keep their previous
    analysis and are reported per photo.
    """
    from .inventory_capture import (
        analyze_batch,
        parse_batch_consent,
        public_entry,
    )

    actor = str(g.user["username"])
    root = current_app.config["DOCUMENT_ROOT"]
    data = request.get_json(silent=True) or request.form
    consent = parse_batch_consent(data.get("consent_meta"))
    try:
        fresh, combined, errors = analyze_batch(
            batch_id, actor, root, consent_meta=consent)
    except CaptureInboxIndexError:
        return jsonify({"ok": False, "error": INDEX_ERROR}), 503
    except ValueError as exc:
        message = str(exc)
        if message == "Buchgruppe ist leer oder unbekannt.":
            abort(404)
        return jsonify({"ok": False, "error": message}), 400
    return jsonify({
        "ok": True,
        "batch_id": str(batch_id or "").strip().lower(),
        "entries": _public_batch_entries(fresh, public_entry),
        "combined": combined,
        "analysis_errors": errors,
        "consent_meta": consent,
    }), 200


@bp.post("/capture/batch/<batch_id>/adopt")
@login_required
def capture_batch_adopt(batch_id: str):
    """Create ONE item from a reviewed book group (correctable, idempotent).

    The user-corrected fields (title, authors, year, publisher, isbn,
    item_type) are validated; the call is idempotent per
    book group + ``capture_request_id`` and safe to retry. A token
    from another group is rejected. All group originals are
    attached as inventory photos and all entries are assigned.
    """
    from .inventory_capture import adopt_batch

    actor = str(g.user["username"])
    root = current_app.config["DOCUMENT_ROOT"]
    data = request.get_json(silent=True) or request.form
    get = data.get
    fields = {
        "title": get("title", ""),
        "authors": get("authors", ""),
        "year": get("year", get("published_date", "")),
        "published_date": get("published_date", get("year", "")),
        "publisher": get("publisher", ""),
        "isbn": get("isbn", ""),
        "barcode": get("barcode", ""),
        "item_type": get("item_type", ""),
        "description": get("description", ""),
        "location": get("location", ""),
    }
    try:
        payload = adopt_batch(
            batch_id, actor, root, fields,
            str(get("capture_request_id", "")),
            allow_duplicate=str(get("allow_duplicate", "")).strip() == "1",
        )
    except CaptureInboxIndexError:
        return jsonify({"ok": False, "error": INDEX_ERROR}), 503
    except PermissionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403
    except ValueError as exc:
        message = str(exc)
        if message == "Buchgruppe ist leer oder unbekannt.":
            abort(404)
        return jsonify({"ok": False, "error": message}), 400
    return jsonify(payload), 200


@bp.post("/capture/<entry_id>/batch")
@login_required
def capture_move(entry_id: str):
    """Move one photo into another book group (or ungroup it).

    Corrects mis-grouped photos before saving; the original,
    analysis, assignment and history are preserved. Idempotent.
    """
    from .capture_inbox_store import CaptureInboxStore
    from .inventory_capture import public_entry

    actor = str(g.user["username"])
    root = current_app.config["DOCUMENT_ROOT"]
    data = request.get_json(silent=True) or request.form
    try:
        known = CaptureInboxStore(root).get(entry_id, actor)
    except CaptureInboxIndexError:
        return jsonify({"ok": False, "error": INDEX_ERROR}), 503
    if known is None:
        abort(404)
    try:
        updated = CaptureInboxStore(root).set_batch(
            entry_id, actor, str(data.get("batch_id", "")))
    except CaptureInboxIndexError:
        return jsonify({"ok": False, "error": INDEX_ERROR}), 503
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if updated is None:
        abort(404)
    item = public_entry(
        updated, url_for("inventory.capture_preview", entry_id=entry_id))
    item["assign_url"] = url_for(
        "inventory.capture_assign", entry_id=entry_id)
    item["analyze_url"] = url_for(
        "inventory.capture_reanalyze", entry_id=entry_id)
    item["batch_url"] = url_for(
        "inventory.capture_move", entry_id=entry_id)
    return jsonify({"ok": True, "entry": item}), 200
