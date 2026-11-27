"""Explicit admin checks for the local Reader and Mail speech toolchain."""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

from flask import Blueprint, abort, current_app, g, render_template, request
from werkzeug.wsgi import LimitedStream

from .admin import admin_required
from .component_runtime import resolve_command

from .document_store_core import ocr_subprocess_environment
from .private_read_aloud import ReadAloudError, configured_piper_model, private_audio_response
from .reader_page_ocr import tesseract_languages
from .reader_speech_text import bounded_tool_output

_POPPLER_TOOLS = ("pdftotext", "pdfimages", "pdftoppm")
_PROBE_SLOT = threading.BoundedSemaphore(1)
bp = Blueprint("reader_runtime", __name__, url_prefix="/admin/settings/documents-media")


def _poppler_status(name: str) -> str:
    tool = shutil.which(name)
    if not tool:
        return "missing"
    try:
        # The Poppler CLIs report their version on stderr; just require a successful exit.
        bounded_tool_output(
            [tool, "-v"], max_output=4096, timeout=2,
            unavailable_code="broken", failure_code="broken", timeout_code="timeout",
        )
    except ReadAloudError as exc:
        return "timeout" if exc.code == "timeout" else "broken"
    return "ready"


def _ocr_languages() -> tuple[str, tuple[str, ...]]:
    binary = shutil.which("tesseract")
    if not binary:
        return "missing", ()
    try:
        languages = tesseract_languages(binary, ocr_subprocess_environment(), 3)
    except ReadAloudError as exc:
        return ("timeout" if exc.code == "ocr_timeout" else "broken"), ()
    supported = tuple(pack for pack in ("deu", "eng") if pack in languages)
    return ("ready" if supported else "language_missing"), supported


def _piper_status(language: str) -> str:
    if not resolve_command("piper"):
        return "missing"
    try:
        model = Path(configured_piper_model(language))
        if not Path(str(model) + ".json").is_file():
            return "config_missing"
    except (ReadAloudError, OSError, ValueError, RuntimeError):
        return "model_unavailable"
    sentence = ("This is the local SimpleOffice voice test." if language.lower().startswith("en") else
                "Dies ist der lokale Stimmtest von SimpleOffice.")
    # The same bounded runtime and cleanup as real speech, but with no document.
    response = private_audio_response(lambda: sentence, language=language)
    if response.status_code == 200:
        return "ready"
    error = (response.get_json() or {}).get("error")
    return {
        "read_aloud_busy": "busy",
        "read_aloud_timeout": "timeout",
    }.get(error, "broken")


def check_reader_runtime(language: str) -> dict[str, object]:
    """Check only fixed executables, known language packs and the saved local voice."""
    if not _PROBE_SLOT.acquire(blocking=False):
        return {"busy": True}
    try:
        tools = {name: _poppler_status(name) for name in _POPPLER_TOOLS}
        tools["tesseract"], packs = _ocr_languages()
        tools["piper"] = _piper_status(language)
        return {"tools": tools, "languages": packs}
    finally:
        _PROBE_SLOT.release()


def limit_request() -> None:
    """Apply the fixed small form limit before global CSRF reads the body."""
    if request.endpoint != "reader_runtime.check":
        return
    if request.mimetype != "application/x-www-form-urlencoded":
        abort(415)
    if not request.content_length:
        abort(411)
    configured_limit = request.max_content_length
    limit = 1024 if configured_limit is None else min(1024, configured_limit)
    if request.content_length > limit:
        abort(413)
    if type(request._get_current_object()).max_content_length.fset is not None:
        request.max_content_length = limit
    elif "wsgi.input_terminated" in request.environ:
        # Flask 3.0 has no per-request limit setter.
        request.environ["wsgi.input"] = LimitedStream(request.environ["wsgi.input"], limit, is_max=True)
    request.max_form_memory_size = limit


@bp.post("/reader-runtime")
@admin_required
def check():
    if request.args or set(request.form) != {"_csrf_token"} or len(request.form.getlist("_csrf_token")) != 1:
        abort(400)
    response = current_app.make_response(render_template(
        "admin/reader_runtime_check.html", reader_runtime=check_reader_runtime(g.language),
    ))
    response.headers["Cache-Control"] = "private, no-store"
    return response
