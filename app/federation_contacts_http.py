"""Federation payload endpoints for contacts as vCard text or safe ZIP bundles."""
from __future__ import annotations

import hmac
import io
import os
import zipfile

from flask import Blueprint, Response, current_app, jsonify, request

from .contact_store import ContactStore


bp = Blueprint("federation_contacts_http", __name__, url_prefix="/federation/v1/contacts")
MAX_VCARD_BYTES = 8 * 1024 * 1024
MAX_ZIP_BYTES = 32 * 1024 * 1024
MAX_ZIP_ENTRIES = 5000
MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024


def _store() -> ContactStore:
    return ContactStore(current_app.config["DOCUMENT_ROOT"])


def _authorized() -> bool:
    expected = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN", "").strip()
    if not expected:
        return bool(current_app.testing)
    header = request.headers.get("Authorization", "")
    supplied = header[7:].strip() if header.startswith("Bearer ") else ""
    return bool(supplied) and hmac.compare_digest(expected, supplied)


@bp.before_request
def authenticate_contacts():
    if not _authorized():
        return Response(
            "federation authentication required\n",
            401,
            {"WWW-Authenticate": 'Bearer realm="SimpleOffice4Me Federation"', "Cache-Control": "no-store"},
        )
    return None


def _actor() -> str:
    return "federation:remote"


def _contact_ids() -> list[str]:
    values = request.args.getlist("id")
    if not values:
        return [contact["contact_id"] for contact in _store().contacts()]
    return [str(value).strip() for value in values if str(value).strip()][:MAX_ZIP_ENTRIES]


@bp.get("/export.vcf")
def export_vcards():
    ids = _contact_ids()
    body = "".join(_store().vcard(contact_id) for contact_id in ids).encode("utf-8")
    if len(body) > MAX_UNCOMPRESSED_BYTES:
        return jsonify({"error": "payload_too_large"}), 413
    return Response(
        body,
        200,
        {
            "Content-Type": "text/vcard; charset=utf-8",
            "Content-Disposition": 'attachment; filename="contacts.vcf"',
            "X-Federation-Resource": "contacts",
            "X-Federation-Format": "vcard-4.0",
        },
    )


@bp.get("/export.txt")
def export_vcards_plain_text():
    ids = _contact_ids()
    body = "".join(_store().vcard(contact_id) for contact_id in ids).encode("utf-8")
    if len(body) > MAX_UNCOMPRESSED_BYTES:
        return jsonify({"error": "payload_too_large"}), 413
    return Response(
        body,
        200,
        {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": 'attachment; filename="contacts.txt"',
            "X-Federation-Resource": "contacts",
            "X-Federation-Format": "vcard-4.0-text",
        },
    )


@bp.get("/export.zip")
def export_vcards_zip():
    ids = _contact_ids()
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for contact_id in ids:
            card = _store().vcard(contact_id).encode("utf-8")
            if len(card) > MAX_VCARD_BYTES:
                return jsonify({"error": "contact_too_large", "contact_id": contact_id}), 413
            safe_id = "".join(char for char in contact_id if char.isalnum() or char in "-_.")[:120] or "contact"
            archive.writestr(f"{safe_id}.vcf", card)
    body = memory.getvalue()
    if len(body) > MAX_ZIP_BYTES:
        return jsonify({"error": "payload_too_large"}), 413
    return Response(
        body,
        200,
        {
            "Content-Type": "application/zip",
            "Content-Disposition": 'attachment; filename="contacts-vcards.zip"',
            "X-Federation-Resource": "contacts",
            "X-Federation-Format": "vcard-zip",
        },
    )


def _decode_utf8(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("vCard payload must be UTF-8") from exc


def _import_zip(data: bytes) -> int:
    if len(data) > MAX_ZIP_BYTES:
        raise ValueError("ZIP payload too large")
    count = 0
    total = 0
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except zipfile.BadZipFile as exc:
        raise ValueError("invalid ZIP payload") from exc
    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_ZIP_ENTRIES:
            raise ValueError("ZIP contains too many entries")
        for entry in entries:
            name = entry.filename.replace("\\", "/")
            if entry.is_dir():
                continue
            if name.startswith("/") or ".." in name.split("/") or not name.casefold().endswith(".vcf"):
                raise ValueError("ZIP may contain only safe .vcf files")
            if entry.file_size < 0 or entry.file_size > MAX_VCARD_BYTES:
                raise ValueError("vCard entry too large")
            total += entry.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("ZIP expands beyond federation limit")
            card = _decode_utf8(archive.read(entry))
            count += _store().import_vcards(card, _actor())
    return count


@bp.post("/import")
def import_contacts():
    media_type = (request.mimetype or "").casefold()
    data = request.get_data(cache=False)
    try:
        if media_type == "application/zip":
            count = _import_zip(data)
            format_name = "vcard-zip"
        elif media_type in {"text/vcard", "text/x-vcard", "text/plain"}:
            if len(data) > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("text payload too large")
            count = _store().import_vcards(_decode_utf8(data), _actor())
            format_name = "vcard-text" if media_type == "text/plain" else "vcard"
        else:
            return jsonify({"error": "unsupported_media_type", "accepted": ["text/vcard", "text/plain", "application/zip"]}), 415
    except ValueError as exc:
        return jsonify({"error": "invalid_contact_payload", "detail": str(exc)}), 400
    return jsonify({"resource": "contacts", "format": format_name, "imported": count})
