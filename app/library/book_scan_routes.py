"""Private, explicit export of a browser-held book-page scan session."""

from hashlib import sha256
import time

from flask import abort, current_app, g, jsonify, make_response, render_template, request, send_file, session

from ..access_control import has_feature, is_admin
from ..auth import login_required
from .book_scan_pdf import MAX_PAGE, MAX_PAGES, MAX_REQUEST, ScanError, build_scan_pdf, ordered_pages
from .book_scan_shares import peers_for, supported_storage
from .routes import bp

SCAN_MESSAGES = {
    "count": ("Wähle 1 bis 24 Buchseiten aus.", "Choose 1 to 24 book pages."),
    "order": ("Seitenreihenfolge ungültig. Bitte Seiten neu auswählen.", "Invalid page order. Select the pages again."),
    "image": ("Bitte nur einzelne JPEG- oder PNG-Bilder verwenden.", "Use single-frame JPEG or PNG images."),
    "size": ("Die Scanauswahl ist zu groß. Weniger oder kleinere Seiten auswählen.",
             "This scan is too large. Select fewer or smaller pages."),
    "resolution": ("Die Seitenauflösung ist zu groß. Weniger oder kleinere Seiten auswählen.",
                   "The page resolution is too large. Select fewer or smaller pages."),
}


def _private(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def limit_upload():
    """Apply limits before the app-wide CSRF validation reads multipart form data."""
    if request.endpoint in {"library.book_scan", "library.book_scan_publications"} and request.method == "POST":
        configured = current_app.config.get("MAX_CONTENT_LENGTH") or MAX_REQUEST
        request.max_content_length = min(configured, MAX_REQUEST)
        request.max_form_parts = MAX_PAGES + 3


def upload_too_large():
    response = jsonify({"code": "size", "error": SCAN_MESSAGES["size"][int(getattr(g, "language", "de") == "en")]})
    response.status_code = 413
    return _private(response)


@bp.route("/book-scan", methods=("GET", "POST"))
@login_required
def book_scan():
    if not has_feature(g.user, "documents"):
        abort(403)
    if request.method == "GET":
        root = current_app.config["DOCUMENT_ROOT"]
        share_peers = peers_for(root, "send") if is_admin(g.user) and supported_storage(root) else []
        return _private(make_response(render_template(
            "library/book_scan.html", page_limit=MAX_PAGES, byte_limit=MAX_PAGE, total_limit=MAX_REQUEST,
            share_peers=share_peers,
        )))
    files = request.files.getlist("pages")
    try:
        order = ordered_pages(request.form.get("order", ""), len(files))
        result = build_scan_pdf(files, order)
    except ScanError as error:
        message = SCAN_MESSAGES[error.code][int(getattr(g, "language", "de") == "en")]
        return _private(jsonify({"error": message, "code": error.code})), error.status
    if g.user["is_admin"]:
        session["book_scan_review"] = {"owner": int(g.user["id"]), "digest": sha256(result.getbuffer()).hexdigest(),
                                       "at": int(time.time())}
    return _private(send_file(result, mimetype="application/pdf", download_name="Buchscan.pdf", as_attachment=True))
