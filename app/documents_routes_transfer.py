"""Document QR and audio transfer routes."""
from __future__ import annotations

import base64
import io
from pathlib import Path

from flask import abort, current_app, g, make_response, render_template, request

from .auth import login_required
from .documents_core import bp, _storage, _document_or_404
from .documents_routes_content import _verified_preview_response
from .v2.contracts import LogicalObjectId
from .document_transfer_access import transferable
from .document_transfer_origin import download_address
from .document_transfer_packet import MAX_PACKET_FILE_BYTES, MAX_AUDIO_PAYLOAD_BYTES, encode_packet
from .virtual_filesystem import VirtualFileSystem

from .quick_share import MAX_PAYLOAD_BYTES as MAX_QR_PAYLOAD_BYTES


def _transfer_document(document_id: str):
    document = _document_or_404(document_id)
    if not transferable(document):
        abort(404)
    logical_path = str(document.get("last_path") or "")
    if not logical_path or not VirtualFileSystem.from_store(
        current_app.config["DOCUMENT_ROOT"],
    ).allows(str(g.user["username"]), logical_path, "read"):
        abort(404)
    readable = _storage(str(g.user["username"])).stat(
        LogicalObjectId(str(document["document_id"])),
    )
    if not readable.ok:
        abort(404)
    return document, readable.value


def _qr_data(payload: str) -> str:
    from .federation_qr_image import render_qr_svg
    svg = render_qr_svg(payload)
    return "data:image/svg+xml;base64," + base64.b64encode(
        svg.encode("utf-8") if isinstance(svg, str) else svg,
    ).decode("ascii")


@bp.route("/<document_id>/transfer", methods=["GET", "POST"])
@login_required
def transfer(document_id: str):
    document, stored = _transfer_document(document_id)
    from .chat_routes import document_transfer_rooms
    chat_rooms = document_transfer_rooms(current_app.config["DOCUMENT_ROOT"], str(g.user["username"]))
    mode = request.form.get("mode", "path")
    if mode not in {"file", "path", "link"}:
        abort(400)
    logical_path = document["last_path"]
    filename = Path(logical_path).name
    payload = download_url = error = ""
    local_only = audio_fallback = False
    if request.method == "POST":
        if mode == "path":
            payload = logical_path
        else:
            download_url, local_only = download_address(document["document_id"])
            payload = download_url
            if mode == "file" and stored.size <= MAX_PACKET_FILE_BYTES:
                content = io.BytesIO()
                storage = _storage(str(g.user["username"]))
                copied = storage.copy_verified_range_to(stored.object_id, content, start=0, length=stored.size)
                if not copied.ok or copied.value.version != stored.version or len(content.getvalue()) != stored.size:
                    abort(404)
                payload = encode_packet(filename, content.getvalue())
                audio_fallback = len(payload) > MAX_AUDIO_PAYLOAD_BYTES
            elif mode == "file":
                error = ("File is too large for a self-contained QR; use the authenticated link."
                         if g.language == "en" else "Die Datei ist zu groß für ein QR-Dateipaket. QR und Ton übertragen den geschützten Link.")
    acoustic_payload = download_url if audio_fallback else payload
    if len(payload.encode("utf-8")) > MAX_QR_PAYLOAD_BYTES:
        qr_src = ""
        error = ("Too long for QR. Use the file link or chat." if g.language == "en"
                 else "Zu lang für QR. Verwende den Datei-Link oder den Chat.")
    else:
        qr_src = _qr_data(payload) if payload else ""
    response = make_response(render_template(
        "documents/transfer.html", document=document, mode=mode, title=filename,
        payload=payload, qr_src=qr_src, download_url=download_url, error=error,
        local_only=local_only, audio_fallback=audio_fallback,
        selected={"payload": acoustic_payload} if payload else None,
        chat_rooms=chat_rooms,
    ))
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.get("/<document_id>/transfer/download")
@login_required
def transfer_download(document_id: str):
    document, _ = _transfer_document(document_id)
    return _verified_preview_response(document, as_attachment=True)
