"""Administrator upload and display for the read-only Fakturama preview."""

from flask import Blueprint, Response, abort, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.wsgi import LimitedStream

from .admin import admin_required
from .fakturama_preview import MAX_FILE_BYTES, PreviewError, example_csv, preview_invoice_csv


bp = Blueprint("fakturama_admin", __name__, url_prefix="/admin/data-migration/fakturama")
MAX_REQUEST_BYTES = MAX_FILE_BYTES + 16 * 1024


def limit_preview_upload() -> None:
    """Bound the multipart envelope before CSRF verification parses it."""
    if request.endpoint != "fakturama_admin.preview" or request.method != "POST":
        return
    if request.mimetype != "multipart/form-data":
        abort(415)
    if request.content_length is None:
        abort(411)
    configured_limit = request.max_content_length
    limit = MAX_REQUEST_BYTES if configured_limit is None else min(MAX_REQUEST_BYTES, configured_limit)
    if request.content_length > limit:
        abort(413)
    if type(request._get_current_object()).max_content_length.fset is not None:
        request.max_content_length = limit
    elif "wsgi.input_terminated" in request.environ:
        # Flask 3.0 has no per-request setter. A terminating WSGI stream may
        # be read past the declared header, so bound the stream itself.
        request.environ["wsgi.input"] = LimitedStream(request.environ["wsgi.input"], limit, is_max=True)
    request.max_form_parts = 2


@bp.after_request
def _private(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.errorhandler(RequestEntityTooLarge)
def _too_large(_error):
    return render_template("admin/fakturama_preview.html", error="file_size"), 413


@bp.route("", methods=("GET", "POST"))
@admin_required
def preview():
    if request.method == "GET":
        return render_template("admin/fakturama_preview.html")
    upload = request.files.get("invoice_csv")
    if upload is None or not upload.filename:
        return render_template("admin/fakturama_preview.html", error="file_missing"), 400
    try:
        result = preview_invoice_csv(upload.stream.read(MAX_FILE_BYTES + 1))
    except PreviewError as exc:
        return render_template("admin/fakturama_preview.html", error=str(exc)), 413 if str(exc) == "file_size" else 400
    return render_template("admin/fakturama_preview.html", result=result)


@bp.get("/profile.csv")
@admin_required
def download_profile():
    return Response(example_csv(), content_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": 'attachment; filename="fakturama-invoice-projection-v1.csv"',
    })
