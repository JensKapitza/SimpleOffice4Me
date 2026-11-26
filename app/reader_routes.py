"""Integrated PDF/EPUB bookshelf and reader routes."""
from __future__ import annotations

from contextlib import contextmanager
import io
import tempfile
import zipfile
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from flask import Blueprint, abort, current_app, flash, g, jsonify, make_response, redirect, render_template, request, send_file, url_for

from .access_control import audit, has_feature
from .auth import login_required
from .document_store import DocumentStore
from .epub_reader import EpubBook, MAX_EPUB_BYTES
from .pdf_limits import MAX_PDF_BYTES
from .private_read_aloud import (
    ReadAloudError, private_audio_response, read_aloud_epub_request, read_aloud_error,
    read_aloud_ocr_request, read_aloud_request,
)
from .reader_page_ocr import pdf_page_has_images, recognize_pdf_page, supplement_native_text
from .reader_speech_text import (
    MAX_READ_ALOUD_PDF_BYTES, MAX_READ_ALOUD_PDF_MIB, MAX_READ_ALOUD_TEXT_BYTES, copy_verified_reader_source, pdf_page_text,
    plain_text_sections, staged_speech_pdf, verified_speech_source,
)
from .reading_store import ReaderVersionChanged, ReadingStateStore, reader_version
from .v2.storage_runtime import storage_for

bp = Blueprint("reader", __name__, url_prefix="/documents/reader")
FORMATS = {".pdf": "pdf", ".epub": "epub"}
MAX_BOOKS = 250
MAX_READ_ALOUD_EPUB_MIB = 16
MAX_READ_ALOUD_EPUB_BYTES = MAX_READ_ALOUD_EPUB_MIB * 1024 * 1024


@bp.after_request
def _keep_reader_html_private(response):
    """Exclude personalized book and shelf HTML from HTTP caches."""
    if request.endpoint in {"reader.shelf", "reader.open_book"} and response.mimetype == "text/html":
        response.headers["Cache-Control"] = "private, no-store"
    return response


class ReaderDocumentRejected(ValueError):
    """A document cannot be safely displayed by the built-in reader."""


def _actor() -> str:
    return str(g.user["username"])


def _require_documents() -> None:
    if not has_feature(g.user, "documents"):
        abort(403)


def _documents() -> DocumentStore:
    return DocumentStore(current_app.config["DOCUMENT_ROOT"])


def _reading() -> ReadingStateStore:
    return ReadingStateStore(current_app.config["DOCUMENT_ROOT"])


def _invalid_reader_input(operation: str, message: str, error: ValueError):
    """Return a fixed client message while logging only safe error metadata."""
    current_app.logger.info(
        "Reader %s request rejected: %s", operation, type(error).__name__,
    )
    return jsonify({"error": message}), 400


def _book(document_id: str) -> tuple[dict[str, Any], str]:
    try:
        document = _documents().get_document(document_id)
    except (ValueError, OSError):
        abort(404)
    kind = FORMATS.get(Path(str(document.get("last_path") or "")).suffix.casefold())
    if not kind:
        abort(404)
    return document, kind


@contextmanager
def _verified_reader_spool(document: dict[str, Any], *, maximum: int) -> Iterator[BinaryIO]:
    # Keep the existing small in-memory spool, but never re-materialize a rolled file.
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as spool:
        storage = storage_for(current_app.config["DOCUMENT_ROOT"], _actor())
        try:
            copy_verified_reader_source(storage, document, spool, maximum)
        except ReadAloudError as exc:
            abort(exc.status)
        spool.seek(0)
        yield spool


def _verified_bytes(document: dict[str, Any], *, maximum: int) -> bytes:
    with _verified_reader_spool(document, maximum=maximum) as spool:
        return spool.read(maximum + 1)


def _current_book_index_text(document: dict[str, Any]) -> str:
    """Match the saved body only if its extraction belongs to the current bytes."""
    digest = document.get("sha256")
    extraction = document.get("text_extraction")
    if (not isinstance(digest, str) or not digest or not isinstance(extraction, dict)
            or extraction.get("source_sha256") != digest
            or extraction.get("status") not in ("completed", "partial")):
        return ""
    text = document.get("extracted_text")
    return text[:2_000_000] if isinstance(text, str) else ""


def _catalog(query: str = "") -> list[dict[str, Any]]:
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
            # sqlite_utils connections use sqlite3's tuple row factory. This
            # is an index-only lookup; get_document revalidates ACL visibility.
            document = store.get_document(str(raw[0]))
            kind = FORMATS.get(Path(str(document.get("last_path") or "")).suffix.casefold())
            if not kind:
                continue
            state = reading.state(str(document["document_id"]), _actor())
        except (KeyError, OSError, ValueError):
            continue
        attributes = document.get("attributes", {}) if isinstance(document.get("attributes"), dict) else {}
        annotation_text = reading.search_text(str(document["document_id"]), _actor())
        row = {
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
            "annotation_text": annotation_text,
        }
        if query:
            # Match indexed text without returning document content to the template.
            content = _current_book_index_text(document)
            searchable = " ".join((row["title"], row["author"], row["path"], annotation_text, content))
            if query not in searchable.casefold():
                continue
        rows.append(row)
        if len(rows) >= MAX_BOOKS:
            break
    return rows


@bp.get("")
@login_required
def shelf():
    from .reading_hints import suggest_continue_reading

    _require_documents()
    query = request.args.get("q", "").strip()[:200].casefold()
    kind = request.args.get("format", "").strip().casefold()
    status = request.args.get("status", "").strip().casefold()
    sort = request.args.get("sort", "recent").strip().casefold()
    rows = _catalog(query)
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
        continue_hints=suggest_continue_reading(rows),
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
            from pypdf import PdfReader

            with _verified_reader_spool(document, maximum=MAX_PDF_BYTES) as spool:
                pdf = PdfReader(spool)
                if pdf.is_encrypted:
                    try:
                        if not pdf.decrypt(""):
                            raise ReaderDocumentRejected(
                                "Passwortgeschützte PDF-Dateien werden im Reader nicht unterstützt."
                            )
                    except ReaderDocumentRejected:
                        raise
                    except Exception as exc:
                        raise ReaderDocumentRejected(
                            "Passwortgeschützte PDF-Dateien werden im Reader nicht unterstützt."
                        ) from exc
                payload["page_count"] = len(pdf.pages)
                metadata = pdf.metadata or {}
                detected_title = str(getattr(metadata, "title", "") or metadata.get("/Title", "") or "")[:500]
                detected_author = str(getattr(metadata, "author", "") or metadata.get("/Author", "") or "")[:500]
    except ReaderDocumentRejected:
        return render_template(
            "reader/error.html", document=document, format=kind,
            message="Passwortgeschützte PDF-Dateien werden im Reader nicht unterstützt.",
        ), 422
    except (ValueError, OSError) as exc:
        current_app.logger.info(
            "Document reader rejected input for %s: %s",
            document_id, type(exc).__name__,
        )
        return render_template(
            "reader/error.html", document=document, format=kind,
            message="Das Dokument konnte nicht sicher verarbeitet werden und wird nicht im Reader angezeigt.",
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
        annotations=annotations, speech_pdf_limit_mib=MAX_READ_ALOUD_PDF_MIB,
        speech_epub_limit_mib=MAX_READ_ALOUD_EPUB_MIB,
    )


def _speech_document(document_id: str) -> tuple[dict[str, Any], str]:
    try:
        document = _documents().get_document(document_id)
    except (ValueError, OSError):
        raise ReadAloudError("document_unavailable", 404) from None
    suffix = Path(str(document.get("last_path") or "")).suffix.casefold()
    if document["document_id"] != document_id or suffix not in {".pdf", ".txt", ".epub"}:
        raise ReadAloudError("document_unavailable", 404)
    return document, suffix


def _speech_source_for(document: dict[str, Any], maximum: int) -> bytes:
    return verified_speech_source(
        storage_for(current_app.config["DOCUMENT_ROOT"], _actor()), document, maximum,
    )


@bp.get("/<document_id>/text")
@login_required
def open_text(document_id: str):
    """Display verified UTF-8 or BOM-marked UTF-16 in bounded sections."""
    _require_documents()
    try:
        document, suffix = _speech_document(document_id)
        if suffix != ".txt":
            raise ReadAloudError("document_unavailable", 404)
        sections = plain_text_sections(_speech_source_for(document, MAX_READ_ALOUD_TEXT_BYTES))
        section = request.args.get("section", 1, type=int)
        if not 1 <= section <= len(sections):
            raise ReadAloudError("invalid_page", 404)
    except ReadAloudError as exc:
        if exc.status == 404:
            abort(404)
        if exc.code == "invalid_text":
            message = ("This file is not supported plain text. Save the original as UTF-8 or as UTF-16 with a BOM."
                       if g.language == "en" else
                       "Diese Datei ist kein unterstützter Klartext. Speichere das Original als UTF-8 oder als UTF-16 mit BOM.")
        else:
            message = ("The text is unavailable or exceeds the Reader limit (256 KiB)." if g.language == "en"
                       else "Der Text ist nicht verfügbar oder überschreitet die Reader-Grenze (256 KiB).")
        response = make_response(render_template(
            "reader/error.html", document=document, format="text", message=message,
        ), exc.status)
    else:
        response = make_response(render_template(
            "reader/text.html", document=document, text=sections[section - 1],
            section=section, section_count=len(sections), document_version=reader_version(document),
        ))
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.post("/<document_id>/read-aloud")
@login_required
def read_aloud(document_id: str):
    """Read native text from an authorized, current document version."""
    return _read_aloud(document_id, allow_ocr=False)


@bp.post("/<document_id>/read-aloud/ocr")
@login_required
def read_aloud_ocr(document_id: str):
    """Recognize the selected PDF scan only on an explicit user request."""
    return _read_aloud(document_id, allow_ocr=True)


def _read_aloud(document_id: str, *, allow_ocr: bool):
    _require_documents()
    try:
        document, suffix = _speech_document(document_id)
        if allow_ocr and suffix != ".pdf":
            raise ReadAloudError("document_unavailable", 404)
        if allow_ocr:
            page, version, clockwise = read_aloud_ocr_request()
            segment = 1
        elif suffix == ".epub":
            page, version, segment = read_aloud_epub_request()
            clockwise = 0
        else:
            page, version = read_aloud_request()
            clockwise, segment = 0, 1
        if version != reader_version(document):
            raise ReadAloudError("document_changed", 409)

        def load_text():
            if suffix == ".pdf":
                storage = storage_for(current_app.config["DOCUMENT_ROOT"], _actor())
                with staged_speech_pdf(storage, document, MAX_READ_ALOUD_PDF_BYTES) as source:
                    return _speech_pdf_page(source, page, g.language, allow_ocr=allow_ocr, clockwise=clockwise)
            if suffix == ".epub":
                source = _speech_source_for(document, MAX_READ_ALOUD_EPUB_BYTES)
                try:
                    parts = EpubBook(source).speech_segments(page - 1)
                except KeyError as exc:
                    raise ReadAloudError("invalid_page", 404) from exc
                except (ValueError, zipfile.BadZipFile) as exc:
                    raise ReadAloudError("invalid_text", 422) from exc
                if not parts:
                    raise ReadAloudError("no_text", 422)
                if segment > len(parts):
                    raise ReadAloudError("invalid_segment", 404)
                return parts[segment - 1]
            sections = plain_text_sections(_speech_source_for(document, MAX_READ_ALOUD_TEXT_BYTES))
            if page > len(sections):
                raise ReadAloudError("invalid_page", 404)
            return sections[page - 1]

        response = private_audio_response(load_text, language=g.language, allow_device_voice=True)
        if response.status_code == 200:
            current, _ = _speech_document(document_id)
            if version != reader_version(current):
                raise ReadAloudError("document_changed", 409)
        return response
    except ReadAloudError as exc:
        return read_aloud_error(exc.code, exc.status)


def _speech_pdf_page(
    source: Path, page: int, language: str, *, allow_ocr: bool, clockwise: int = 0,
) -> str:
    text = pdf_page_text(source, page)
    if not allow_ocr or (text.strip() and not pdf_page_has_images(source, page)):
        return text
    try:
        recognized = (recognize_pdf_page(source, page, language, clockwise=clockwise)
                      if clockwise else recognize_pdf_page(source, page, language))
    except ReadAloudError as exc:
        if exc.code == "ocr_no_text" and text.strip():
            return text
        raise
    return supplement_native_text(text, recognized)


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


@bp.post("/<document_id>/pdf/grayscale")
@login_required
def grayscale_pdf(document_id: str):
    """Download a grayscale copy, retaining the protected source unchanged."""
    from .pdf_limits import PdfConversionTimeout

    _require_documents()
    document, kind = _book(document_id)
    if kind != "pdf":
        abort(404)
    source = _verified_bytes(document, maximum=MAX_PDF_BYTES)
    try:
        from .pdf_conversion import make_grayscale_searchable_pdf

        payload = make_grayscale_searchable_pdf(source)
    except PdfConversionTimeout:
        current_app.logger.info("Reader PDF export timed out for %s", document_id)
        flash(
            "The local PDF export took too long. Try again or split the PDF into smaller files. Your original is unchanged."
            if g.language == "en" else
            "Der lokale PDF-Export hat das Zeitlimit überschritten. Versuche es erneut oder teile das PDF "
            "in kleinere Dateien. Dein Original bleibt unverändert.",
            "reader-export-error",
        )
        return redirect(url_for("reader.open_book", document_id=document_id))
    except ValueError as exc:
        current_app.logger.info(
            "Reader PDF conversion rejected for %s: %s",
            document_id, type(exc).__name__,
        )
        flash("Das Graustufen-PDF konnte nicht erstellt werden. Prüfe das PDF-Format und die installierte PDF-Unterstützung.")
        return redirect(url_for("reader.open_book", document_id=document_id))
    except (OSError, RuntimeError) as exc:
        current_app.logger.warning(
            "Reader PDF conversion failed for %s: %s",
            document_id, type(exc).__name__,
        )
        flash(
            "Could not create the searchable copy. Check local Ghostscript, Poppler and OCR setup. "
            "To read only this page, use ‘OCR and read aloud’."
            if g.language == "en" else
            "Die durchsuchbare Kopie konnte nicht erstellt werden. Prüfe die lokale Installation "
            "von Ghostscript, Poppler und OCR. Für diese einzelne Seite nutze „OCR und vorlesen“."
        )
        return redirect(url_for("reader.open_book", document_id=document_id))

    stem = Path(str(document.get("last_path") or "document.pdf")).stem[:100]
    audit(
        "document_reader_pdf_grayscale_export", "document", document_id,
        detail={"ocr_layer": "invisible", "source_changed": False},
    )
    response = send_file(
        io.BytesIO(payload), mimetype="application/pdf", as_attachment=True,
        download_name=f"{stem}-sw-ocr.pdf", max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
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
    except ReaderVersionChanged:
        return jsonify({"error": "Das Dokument wurde geändert. Bitte lade den Reader neu."}), 409
    except ValueError as exc:
        return _invalid_reader_input(
            "progress", "Ungültige Leseposition oder Fortschrittsangabe.", exc,
        )
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
    except ReaderVersionChanged:
        return jsonify({"error": "Das Dokument wurde geändert. Bitte lade den Reader neu."}), 409
    except ValueError as exc:
        return _invalid_reader_input(
            "annotation", "Ungültige Leseposition oder Anmerkung.", exc,
        )
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
