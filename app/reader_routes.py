"""Integrated PDF/EPUB bookshelf and reader routes."""
from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import Any

from flask import Blueprint, abort, current_app, g, jsonify, render_template, request

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
        rows.append({
            "document_id": str(document["document_id"]),
            "title": str(document.get("attributes", {}).get("title") or Path(str(document.get("last_path") or "")).stem),
            "path": str(document.get("last_path") or ""),
            "format": kind,
            "size": int(document.get("size", 0) or 0),
            "version_number": int(document.get("version_number", 1) or 1),
            "updated_at": str(document.get("last_seen_at", "")),
            "progress": int(state.get("percent", 0) or 0),
            "stale": bool(state.get("stale")),
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
        rows = [row for row in rows if query in (row["title"] + " " + row["path"]).casefold()]
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
    elif sort == "progress":
        rows.sort(key=lambda row: (row["progress"], row["title"].casefold()), reverse=True)
    else:
        rows.sort(key=lambda row: row["updated_at"], reverse=True)
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
    payload: dict[str, Any] = {
        "title": Path(str(document.get("last_path") or "")).stem,
        "format": kind,
        "version": reader_version(document),
        "page_count": 0,
        "toc": [],
    }
    if kind == "epub":
        book = EpubBook(_verified_bytes(document, maximum=MAX_EPUB_BYTES))
        payload.update(book.metadata())
        payload["toc"] = book.toc()
    else:
        raw = _verified_bytes(document, maximum=200 * 1024 * 1024)
        try:
            from pypdf import PdfReader
            payload["page_count"] = len(PdfReader(io.BytesIO(raw)).pages)
        except Exception:
            payload["page_count"] = 0
    _documents().record_access(document_id, _actor(), "seen")
    audit(
        "document_reader_open", "document", document_id,
        detail={"format": kind, "document_version": payload["version"]},
    )
    return render_template(
        "reader/book.html", document=document, book=payload, reader_state=state,
        annotations=annotations,
    )


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
