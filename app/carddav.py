"""Small authenticated CardDAV address book for Thunderbird-compatible clients."""

from __future__ import annotations

import hashlib
from xml.sax.saxutils import escape

from flask import Blueprint, Response, current_app, request

from .contact_store import ContactStore


bp = Blueprint("carddav", __name__, url_prefix="/carddav")
DAV = "DAV:"
CARD = "urn:ietf:params:xml:ns:carddav"


def _store() -> ContactStore:
    return ContactStore(current_app.config["DOCUMENT_ROOT"])


def _auth() -> str | None:
    credentials = request.authorization
    if credentials and credentials.type.lower() == "basic" and _store().carddav_authenticate(credentials.username, credentials.password):
        return credentials.username
    return None


def _unauthorized() -> Response:
    return Response("CardDAV authentication required", 401, {"WWW-Authenticate": 'Basic realm="SimpleOffice4Me CardDAV"'})


def _xml(items: list[tuple[str, str]]) -> Response:
    responses = "".join(f"<d:response><d:href>{escape(href)}</d:href><d:propstat><d:prop>{properties}</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>" for href, properties in items)
    return Response(f'<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="{DAV}" xmlns:card="{CARD}">{responses}</d:multistatus>', 207, mimetype="application/xml; charset=utf-8")


def _etag(contact: dict) -> str:
    return '"' + hashlib.sha256((contact["contact_id"] + contact.get("updated_at", "")).encode()).hexdigest() + '"'


def _write_privileges() -> str:
    """Advertise writable DAV permissions so clients do not mark the book read-only."""
    return "<d:current-user-privilege-set><d:privilege><d:read/></d:privilege><d:privilege><d:write/></d:privilege><d:privilege><d:write-content/></d:privilege><d:privilege><d:bind/></d:privilege><d:privilege><d:unbind/></d:privilege></d:current-user-privilege-set>"


@bp.route("/", defaults={"path": ""}, methods=["OPTIONS", "PROPFIND", "REPORT", "GET", "PUT", "DELETE"])
@bp.route("/<path:path>", methods=["OPTIONS", "PROPFIND", "REPORT", "GET", "PUT", "DELETE"])
def endpoint(path: str):
    username = _auth()
    if username is None:
        return _unauthorized()
    base = f"/carddav/addressbooks/{username}/default/"
    if request.method == "OPTIONS":
        return Response("", 204, {"DAV": "1, addressbook", "Allow": "OPTIONS, PROPFIND, REPORT, GET, PUT, DELETE"})
    normalized = path.strip("/")
    if normalized.startswith("addressbooks/") and not normalized.startswith(f"addressbooks/{username}/"):
        return Response("not found", 404)
    if request.method == "PROPFIND":
        if normalized.endswith(".vcf"):
            contact_id = normalized.rsplit("/", 1)[-1][:-4]
            try: contact = _store().get(contact_id)
            except ValueError: return Response("not found", 404)
            return _xml([(request.path, f"<d:getetag>{_etag(contact)}</d:getetag><d:getcontenttype>text/vcard; charset=utf-8</d:getcontenttype>{_write_privileges()}")])
        return _xml([(base, f"<d:resourcetype><d:collection/><card:addressbook/></d:resourcetype><d:displayname>SimpleOffice Kontakte</d:displayname>{_write_privileges()}")])
    if request.method == "REPORT":
        items = []
        for contact in _store().contacts():
            href = base + contact["contact_id"] + ".vcf"
            card = escape(_store().vcard(contact["contact_id"]))
            items.append((href, f"<d:getetag>{_etag(contact)}</d:getetag><card:address-data content-type=\"text/vcard\" version=\"4.0\">{card}</card:address-data>"))
        return _xml(items)
    if normalized.endswith(".vcf"):
        contact_id = normalized.rsplit("/", 1)[-1][:-4]
        if request.method == "GET":
            try: card = _store().vcard(contact_id); contact = _store().get(contact_id)
            except ValueError: return Response("not found", 404)
            return Response(card, 200, {"Content-Type": "text/vcard; charset=utf-8", "ETag": _etag(contact)})
        if request.method == "PUT":
            try:
                _store().get(contact_id)
                created = False
            except ValueError:
                created = True
            contact = _store().upsert_vcard(request.get_data(as_text=True), f"carddav:{username}", contact_id)
            return Response("", 201 if created else 204, {"ETag": _etag(contact), "Location": base + contact["contact_id"] + ".vcf"})
        if request.method == "DELETE":
            try: _store().delete(contact_id, f"carddav:{username}")
            except ValueError: return Response("not found", 404)
            return Response("", 204)
    return Response("not found", 404)
