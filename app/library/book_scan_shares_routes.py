"""Opt-in, exact-version book scan offer and explicit recipient fetch."""

from __future__ import annotations

from hashlib import sha256
import io
import re
import time
from urllib.error import HTTPError, URLError

from flask import Blueprint, abort, current_app, g, jsonify, render_template, request, send_file, session

from ..access_control import has_feature, is_admin
from ..auth import login_required
from ..federation_peer_auth import authenticate
from ..federation_store import FederationStore
from ..federation_worker import _request
from .book_scan_pdf import MAX_REQUEST, ScanError
from .book_scan_routes import _private
from .book_scan_shares import Shares, eligible_peer, offer_id, peers_for, supported_storage
from .routes import bp


federation_bp = Blueprint("federation_book_scan_offer", __name__, url_prefix="/federation/v1/book-scans")
HASH = re.compile(r"[0-9a-f]{64}")
REVIEW_SECONDS = 900
MESSAGES = {
    "confirm": ("Bestätige zuerst die gezielte Freigabe.", "Confirm this specific publication first."),
    "changed": ("Wähle das zuletzt erzeugte PDF unverändert aus.", "Select the last generated PDF unchanged."),
    "peer": ("Diese Gegenstelle hat keine passende Freigabe.", "This peer does not have the required permission."),
    "offer": ("Das Angebot ist nicht verfügbar.", "This offer is unavailable."),
    "storage": ("Diese Speicherart unterstützt keine Scanner-Freigabe.", "This storage mode does not support scan publication."),
    "size": ("Das PDF überschreitet 32 MiB.", "The PDF exceeds 32 MiB."),
    "quota": ("Zu viele aktive Angebote. Entferne ein Angebot und versuche es erneut.",
              "Too many active offers. Revoke one and try again."),
    "rate": ("Zu viele neue Freigaben. Warte bis zu 15 Minuten; Abrufen und Widerruf bleiben möglich.",
             "Too many recent publications. Wait up to 15 minutes; retrieval and revocation still work."),
    "replace": ("Nur ein eigenes aktives Angebot an denselben Peer kann ersetzt werden.",
                "Only your own active offer to the same peer can be replaced."),
    "receive": ("PDF konnte nicht sicher abgeholt werden. Prüfe die Verbindung und versuche es erneut.",
                "The PDF could not be securely retrieved. Check connectivity and try again."),
}


def _root():
    return current_app.config["DOCUMENT_ROOT"]


def _download(data):
    return _private(send_file(io.BytesIO(data), as_attachment=True, mimetype="application/pdf",
                              download_name="Buchscan.pdf"))


def _publish(request_form, owner: int):
    if not supported_storage(_root()):
        raise ScanError("storage", 409)
    review = session.get("book_scan_review", {})
    if request_form.get("confirm") != "1":
        raise ScanError("confirm", 400)
    if review.get("owner") != owner or not 0 <= int(time.time()) - review.get("at", 0) < REVIEW_SECONDS:
        raise ScanError("changed", 409)
    file = request.files.get("pdf")
    if file is None:
        raise ScanError("changed", 409)
    return Shares(_root()).publish(owner, request_form.get("peer_id", ""), file.stream.read(MAX_REQUEST + 1),
                                   review.get("digest", ""), replace=request_form.get("replace_offer", ""))


def _receive(peer_id, raw_offer):
    selected = eligible_peer(_root(), peer_id, "receive")
    code = offer_id(raw_offer)
    target = selected["base_url"].rstrip("/") + f"/federation/v1/book-scans/{code}/pdf"
    try:
        with _request(target, token=FederationStore(_root()).peer_token(peer_id), timeout=35) as response:
            size = int(response.headers.get("Content-Length") or 0)
            digest = response.headers.get("X-Book-Scan-SHA256", "")
            if not 0 < size <= MAX_REQUEST or not HASH.fullmatch(digest):
                raise ScanError("receive", 502)
            data = response.read(size + 1)
        if len(data) != size or sha256(data).hexdigest() != digest or not data.startswith(b"%PDF-"):
            raise ScanError("receive", 502)
    except ScanError:
        raise
    except HTTPError as exc:
        exc.close()
        if exc.code in {401, 403, 404, 410}:
            raise ScanError("offer", 404) from exc
        raise ScanError("receive", 502) from exc
    except (ValueError, OSError, URLError) as exc:
        raise ScanError("receive", 502) from exc
    return _download(data)


def _publication_page(shares, owner: int, *, result=None, error="", retry_peer="", retry_offer=""):
    can_publish = supported_storage(_root())
    return _private(current_app.make_response(render_template(
        "library/book_scan_publications.html", offers=shares.list_owned(owner), result=result, error=error,
        send_storage_supported=can_publish, send_peers=peers_for(_root(), "send") if can_publish else [],
        receive_peers=peers_for(_root(), "receive"), retry_peer=retry_peer, retry_offer=retry_offer,
    )))


@bp.route("/book-scan/publications", methods=("GET", "POST"))
@login_required
def book_scan_publications():
    if not is_admin(g.user) or not has_feature(g.user, "documents"):
        abort(403)
    user_id = int(g.user["id"])
    shares = Shares(_root())
    if request.method == "GET":
        shares.prune_if_due()
        return _publication_page(shares, user_id)
    action = request.form.get("action")
    try:
        if action == "publish":
            return _publication_page(shares, user_id, result=_publish(request.form, user_id))
        if action == "revoke":
            shares.revoke(user_id, request.form.get("offer", ""))
            return _publication_page(shares, user_id)
        if action == "receive":
            return _receive(request.form.get("peer_id", ""), request.form.get("offer", ""))
        raise ScanError("offer", 400)
    except ScanError as error:
        message = MESSAGES[error.code][int(getattr(g, "language", "de") == "en")]
        retry_peer = request.form.get("peer_id", "")[:128] if action == "receive" else ""
        retry_offer = request.form.get("offer", "")[:36] if action == "receive" else ""
        response = _publication_page(shares, user_id, error=message, retry_peer=retry_peer, retry_offer=retry_offer)
        response.status_code = error.status
        if error.status == 429:
            response.headers["Retry-After"] = "900"
        return response


@federation_bp.get("/<code>/pdf")
def receive_pdf(code: str):
    root = _root()
    try:
        peer = authenticate(root, request)
    except (ValueError, TypeError):
        abort(401)
    try:
        data, digest = Shares(root).read(peer, code)
    except ScanError:
        abort(404)
    response = _download(data)
    response.headers["X-Book-Scan-SHA256"] = digest
    return response
