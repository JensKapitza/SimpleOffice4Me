"""Integrated PDF/EPUB bookshelf and reader routes."""
from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import Any

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, send_file, url_for

from .access_control import audit, has_feature
from .auth import login_required
from .document_store import DocumentStore
from .epub_reader import EpubBook, MAX_EPUB_BYTES
from .reading_store import ReadingStateStore, reader_version
from .v2.contracts import LogicalObjectId
from .v2.storage_runtime import storage_for

bp = Blueprint("reader", __name__, url_prefix="/documents/reader")
FORMATS = {".pdf": "pdf", ".epub": "epub"}
MAX_BOOKS = 250


def _actor() -> str:
    return str(g.user["username"])


def _require_documents() -> None:
    if not has_feature(g.user, "documents"):
        abort(403)


def _documents() -> DocumentStore:
    return DocumentStore(current_app.config["DOCUMENT_ROOT"])


def _reading() -> ReadingStateStore:
    return ReadingStateStore(current_app.config["DOCUMENT_ROOT"])


def _book(document_id: str) -> tuple[dict[str, Any], str]:
    document = _documents().get_document(document_id)
    kind = FORMATS.get(Path(str(document.get("last_path") or "")).suffix.casefold())
    if not kind:
        abort(404)
    return document, kind


def _verified_bytes(document: dict[str, Any], *, maximum: int) -> bytes:
    spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    result = storage_for(current_app.config["DOCUMENT_ROOT"], _actor()).copy_verified_to(
        LogicalObjectId(str(document["document_id"])), spool,
    )
    if not result.ok:
        spool.close()
        abort(404)
    size = int(result.value.size)
    if size > maximum:
        spool.close()
        abort(413)
    spool.seek(0)
    payload = spool.read(maximum + 1)
    spool.close()
    if len(payload) != size or len(payload) > maximum:
        abort(413)
    return payload


def _catalog() -> list[dict[str, Any]]:
    store = _documents()
    rows: list[dict[str, Any]] = []
    # Query the existing repairable listing index, then revalidate each document
    # through get_document so chat/private visibility remains fail-closed.
    with store._db() as db:
        ids = db.execute(
            """SELECT document_id FROM document_listing
               WHERE lower(path) LIKE '%.pdf' OR lower(path) LIKE '%.epub'
               ORDER BY last_seen_at DESC LIMIT ?""",
            (MAX_BOOKS * 3,),
        ).fetchall()
    reading = _reading()
    for raw in ids:
        try:
            document = store.get_document(str(raw["document_id"]))
            kind = FORMATS.get(Path(str(document.get("last_path") or "")).suffix.casefold())
            if not kind:
                continue
            state = reading.state(str(document["document_id"]), _actor())
        except (KeyError, OSError, ValueError):
            continue
        attributes = document.get("attributes", {}) if isinstance(document.get("attributes"), dict) else {}
        rows.append({
            "document_id": str(document["document_id"]),
            "title": str(
                attributes.get("reader_title")
                or attributes.get("reader_detected_title")
                or Path(str(document.get("last_path") or "")).stem
            )[:500],
            "author": str(
                attributes.get("reader_author")
                or attributes.get("reader_detected_author")
                or ""
            )[:500],
            "path": str(document.get("last_path") or ""),
            "format": kind,
            "size": int(document.get("size", 0) or 0),
            "version_number": int(document.get("version_number", 1) or 1),
            "updated_at": str(document.get("last_seen_at", "")),
            "last_read_at": str(state.get("updated_at", "")),
            "progress": int(state.get("percent", 0) or 0),
            "stale": bool(state.get("stale")),
            "annotation_text": reading.search_text(str(document["document_id"]), _actor()),
        })
        if len(rows) >= MAX_BOOKS:
            break
    return rows


@bp.get("")
@login_required
def shelf():
    _require_documents()
    query = request.args.get("q", "").strip().casefold()
    kind = request.args.get("format", "").strip().casefold()
    status = request.args.get("status", "").strip().casefold()
    sort = request.args.get("sort", "recent").strip().casefold()
    rows = _catalog()
    if query:
        rows = [
            row for row in rows
            if query in (
                row["title"] + " " + row["author"] + " " + row["path"] + " " + row["annotation_text"]
            ).casefold()
        ]
    if kind in {"pdf", "epub"}:
        rows = [row for row in rows if row["format"] == kind]
    if status == "unread":
        rows = [row for row in rows if row["progress"] == 0]
    elif status == "reading":
        rows = [row for row in rows if 0 < row["progress"] < 100]
    elif status == "finished":
        rows = [row for row in rows if row["progress"] >= 100]
    if sort == "title":
        rows.sort(key=lambda row: row["title"].casefold())
    elif sort == "author":
        rows.sort(key=lambda row: (row["author"].casefold(), row["title"].casefold()))
    elif sort == "progress":
        rows.sort(key=lambda row: (row["progress"], row["title"].casefold()), reverse=True)
    else:
        rows.sort(
            key=lambda row: (row["last_read_at"] or row["updated_at"], row["title"].casefold()),
            reverse=True,
        )
    return render_template(
        "reader/shelf.html", books=rows, query=request.args.get("q", ""),
        format_filter=kind, status_filter=status, sort=sort,
    )


@bp.get("/<document_id>")
@login_required
def open_book(document_id: str):
    _require_documents()
    document, kind = _book(document_id)
    state = _reading().state(document_id, _actor())
    annotations = _reading().annotations(document_id, _actor())
    attributes = document.get("attributes", {}) if isinstance(document.get("attributes"), dict) else {}
    payload: dict[str, Any] = {
        "title": Path(str(document.get("last_path") or "")).stem,
        "author": "",
        "format": kind,
        "version": reader_version(document),
        "page_count": 0,
        "toc": [],
    }
    detected_title = ""
    detected_author = ""
    try:
        if kind == "epub":
            book = EpubBook(_verified_bytes(document, maximum=MAX_EPUB_BYTES))
            detected = book.metadata()
            detected_title = str(detected.get("title", ""))[:500]
            detected_author = str(detected.get("author", ""))[:500]
            payload.update(detected)
            payload["toc"] = book.toc()
        else:
            raw = _verified_bytes(document, maximum=200 * 1024 * 1024)
            from pypdf import PdfReader
            pdf = PdfReader(io.BytesIO(raw))
            if pdf.is_encrypted:
                try:
                    if not pdf.decrypt(""):
                        raise ValueError("Passwortgeschützte PDF-Dateien werden im Reader nicht unterstützt.")
                except Exception as exc:
                    raise ValueError("Passwortgeschützte PDF-Dateien werden im Reader nicht unterstützt.") from exc
            payload["page_count"] = len(pdf.pages)
            metadata = pdf.metadata or {}
            detected_title = str(getattr(metadata, "title", "") or metadata.get("/Title", "") or "")[:500]
            detected_author = str(getattr(metadata, "author", "") or metadata.get("/Author", "") or "")[:500]
    except (ValueError, OSError) as exc:
        return render_template(
            "reader/error.html", document=document, format=kind, message=str(exc),
        ), 422
    except Exception as exc:
        current_app.logger.warning(
            "Document reader parse failed for %s: %s", document_id, type(exc).__name__
        )
        return render_template(
            "reader/error.html", document=document, format=kind,
            message="Das Dokument konnte nicht sicher für den Reader verarbeitet werden.",
        ), 422

    payload["title"] = str(
        attributes.get("reader_title") or detected_title or payload.get("title") or Path(str(document.get("last_path") or "")).stem
    )[:500]
    payload["author"] = str(
        attributes.get("reader_author") or detected_author or payload.get("author") or ""
    )[:500]
    detected_updates = {}
    if detected_title and attributes.get("reader_detected_title") != detected_title:
        detected_updates["reader_detected_title"] = detected_title
    if detected_author and attributes.get("reader_detected_author") != detected_author:
        detected_updates["reader_detected_author"] = detected_author
    if detected_updates:
        try:
            _documents().update_metadata(document_id, attributes=detected_updates, author=_actor())
        except (OSError, ValueError, PermissionError):
            pass
    _documents().record_access(document_id, _actor(), "seen")
    audit(
        "document_reader_open", "document", document_id,
        detail={"format": kind, "document_version": payload["version"]},
    )
    return render_template(
        "reader/book.html", document=document, book=payload, reader_state=state,
        annotations=annotations,
    )


@bp.get("/<document_id>/cover")
@login_required
def epub_cover(document_id: str):
    _require_documents()
    document, kind = _book(document_id)
    if kind != "epub":
        abort(404)
    try:
        cover = EpubBook(_verified_bytes(document, maximum=MAX_EPUB_BYTES)).cover()
    except ValueError:
        abort(404)
    if cover is None:
        abort(404)
    payload, media_type = cover
    response = send_file(
        io.BytesIO(payload), mimetype=media_type, as_attachment=False,
        download_name="cover", max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


@bp.post("/<document_id>/metadata")
@login_required
def update_reader_metadata(document_id: str):
    _require_documents()
    document, _kind = _book(document_id)
    title = request.form.get("title", "").strip()
    author = request.form.get("author", "").strip()
    if len(title) > 500 or len(author) > 500:
        return jsonify({"error": "metadata_too_long"}), 400
    try:
        _documents().update_metadata(
            document["document_id"],
            attributes={"reader_title": title, "reader_author": author},
            author=_actor(),
        )
    except (ValueError, PermissionError, OSError) as exc:
        current_app.logger.warning(
            "Reader metadata update denied for %s: %s", document_id, type(exc).__name__
        )
        flash("Reader-Metadaten konnten für dieses Dokument nicht geändert werden.")
        return redirect(url_for("reader.open_book", document_id=document_id))
    audit(
        "document_reader_metadata", "document", document_id,
        detail={"title_set": bool(title), "author_set": bool(author)},
    )
    flash("Reader-Metadaten wurden gespeichert.")
    return redirect(url_for("reader.open_book", document_id=document_id))


@bp.get("/<document_id>/epub/<int:index>")
@login_required
def epub_chapter(document_id: str, index: int):
    _require_documents()
    document, kind = _book(document_id)
    if kind != "epub":
        abort(404)
    try:
        chapter = EpubBook(_verified_bytes(document, maximum=MAX_EPUB_BYTES)).chapter(index)
    except (KeyError, ValueError):
        abort(404)
    response = jsonify(chapter)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.post("/api/<document_id>/progress")
@login_required
def save_progress(document_id: str):
    _require_documents()
    _book(document_id)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "invalid_request"}), 400
    try:
        state = _reading().save_progress(
            document_id, _actor(), data.get("locator"), data.get("percent", 0),
            str(data.get("document_version", "")),
        )
    except ValueError as exc:
        code = 409 if "version changed" in str(exc) else 400
        return jsonify({"error": str(exc)}), code
    audit(
        "document_reader_progress", "document", document_id,
        detail={"format": state["format"], "percent": state["percent"]},
    )
    return jsonify(state)


@bp.post("/api/<document_id>/annotations")
@login_required
def add_annotation(document_id: str):
    _require_documents()
    _book(document_id)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "invalid_request"}), 400
    try:
        row = _reading().add_annotation(
            document_id, _actor(), data.get("locator"), str(data.get("text", "")),
            str(data.get("document_version", "")),
            annotation_kind=str(data.get("kind", "note")),
            quote=str(data.get("quote", "")),
        )
    except ValueError as exc:
        code = 409 if "version changed" in str(exc) else 400
        return jsonify({"error": str(exc)}), code
    audit("document_reader_annotation", "document", document_id, detail={"annotation_id": row["annotation_id"]})
    return jsonify(row), 201


@bp.post("/api/<document_id>/annotations/<annotation_id>/delete")
@login_required
def delete_annotation(document_id: str, annotation_id: str):
    _require_documents()
    _book(document_id)
    try:
        _reading().delete_annotation(document_id, _actor(), annotation_id)
    except KeyError:
        abort(404)
    return jsonify({"ok": True})
