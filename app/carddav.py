"""Small authenticated CardDAV address book for Thunderbird-compatible clients."""

from __future__ import annotations

import hashlib
import json
from xml.sax.saxutils import escape

from flask import Blueprint, Response, current_app, request, url_for

from .contact_store import ContactConflict, ContactStore


bp = Blueprint("carddav", __name__)
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


def _etag_matches(header_value: str, current_etag: str) -> bool:
    """Apply HTTP entity-tag list matching for CardDAV write preconditions."""
    return any(candidate.strip() == "*" or candidate.strip() == current_etag for candidate in header_value.split(","))


def _precondition_failed(current_etag: str = "") -> Response:
    headers = {"ETag": current_etag} if current_etag else {}
    return Response("CardDAV precondition failed", 412, headers)


def _privileges(writable: bool = True) -> str:
    values = ["<d:privilege><d:read/></d:privilege>"]
    if writable:
        values.extend([
            "<d:privilege><d:write/></d:privilege>",
            "<d:privilege><d:write-content/></d:privilege>",
            "<d:privilege><d:bind/></d:privilege>",
            "<d:privilege><d:unbind/></d:privilege>",
        ])
    return "<d:current-user-privilege-set>" + "".join(values) + "</d:current-user-privilege-set>"


def _addressbook_properties() -> str:
    return f'<d:resourcetype><d:collection/><card:addressbook/></d:resourcetype><d:displayname>SimpleOffice Kontakte</d:displayname><card:supported-address-data><card:address-data-type content-type="text/vcard" version="4.0"/></card:supported-address-data>{_privileges(True)}'


def _canonicalize_collection(path: str, username: str) -> str:
    """Accept the historical /contacts/ collection name but use /default/ internally."""
    normalized = path.strip("/")
    legacy = f"addressbooks/{username}/contacts"
    canonical = f"addressbooks/{username}/default"
    if normalized == legacy:
        return canonical
    if normalized.startswith(legacy + "/"):
        return canonical + normalized[len(legacy):]
    return normalized


def _diagnostics(store: ContactStore, username: str) -> dict:
    all_contacts = store.contacts()
    visible = store.contacts(username)
    ownerless = 0
    inaccessible = 0
    foreign_owners: set[str] = set()
    for contact in all_contacts:
        owner = str(contact.get("owner", "")).strip() or store._principal(str(contact.get("created_by", "")))
        if not owner:
            ownerless += 1
        elif owner != username:
            foreign_owners.add(owner)
        if contact not in visible:
            inaccessible += 1

    issues: list[dict[str, str]] = []
    if not all_contacts:
        issues.append({"code": "no_contacts", "detail": "Im Kontaktbestand sind keine Kontakte gespeichert."})
    elif not visible:
        issues.append({"code": "no_visible_contacts", "detail": "Kontakte sind vorhanden, aber fuer diesen CardDAV-Benutzer nicht freigegeben."})
    elif inaccessible:
        issues.append({"code": "partially_visible", "detail": f"{inaccessible} Kontakt(e) sind fuer diesen CardDAV-Benutzer nicht sichtbar."})
    if ownerless:
        issues.append({"code": "owner_missing", "detail": f"{ownerless} Kontakt(e) haben keine auswertbare Eigentuemerk Zuordnung."})

    return {
        "status": "ok" if not issues else "warning",
        "authenticated_user": username,
        "canonical_collection": f"/carddav/addressbooks/{username}/default/",
        "legacy_collection": f"/carddav/addressbooks/{username}/contacts/",
        "contacts_total": len(all_contacts),
        "contacts_visible": len(visible),
        "contacts_inaccessible": inaccessible,
        "contacts_ownerless": ownerless,
        "foreign_owner_count": len(foreign_owners),
        "issues": issues,
    }


@bp.route("/.well-known/carddav", methods=["OPTIONS", "PROPFIND", "GET"])
def well_known():
    """Redirect CardDAV auto-discovery to the authenticated DAV context."""
    return Response("", 307, {"Location": url_for("carddav.endpoint", path="", _external=True), "Cache-Control": "public, max-age=3600"})


@bp.route("/carddav/", defaults={"path": ""}, methods=["OPTIONS", "PROPFIND", "REPORT", "GET", "PUT", "DELETE"])
@bp.route("/carddav/<path:path>", methods=["OPTIONS", "PROPFIND", "REPORT", "GET", "PUT", "DELETE"])
def endpoint(path: str):
    username = _auth()
    if username is None:
        return _unauthorized()
    store = _store()
    base = f"/carddav/addressbooks/{username}/default/"
    if request.method == "OPTIONS":
        return Response("", 204, {"DAV": "1, addressbook", "Allow": "OPTIONS, PROPFIND, REPORT, GET, PUT, DELETE"})
    normalized = _canonicalize_collection(path, username)
    if normalized == "diagnostics" and request.method == "GET":
        return Response(json.dumps(_diagnostics(store, username), ensure_ascii=False, indent=2), 200, mimetype="application/json")
    if normalized.startswith("addressbooks/") and normalized != f"addressbooks/{username}" and not normalized.startswith(f"addressbooks/{username}/"):
        return Response("not found", 404)
    if normalized.startswith("principals/") and normalized != f"principals/{username}":
        return Response("not found", 404)
    if request.method == "PROPFIND":
        if normalized.endswith(".vcf"):
            contact_id = normalized.rsplit("/", 1)[-1][:-4]
            try: contact = store.get(contact_id, username)
            except ValueError: return Response("not found", 404)
            writable = store.can_manage(contact_id, username)
            return _xml([(base + contact_id + ".vcf", f"<d:getetag>{_etag(contact)}</d:getetag><d:getcontenttype>text/vcard; charset=utf-8</d:getcontenttype>{_privileges(writable)}")])
        principal = url_for("carddav.endpoint", path=f"principals/{username}/", _external=True)
        home = url_for("carddav.endpoint", path=f"addressbooks/{username}/", _external=True)
        addressbook = url_for("carddav.endpoint", path=f"addressbooks/{username}/default/", _external=True)
        if not normalized:
            return _xml([(request.url, f"<d:resourcetype><d:collection/></d:resourcetype><d:current-user-principal><d:href>{escape(principal)}</d:href></d:current-user-principal>")])
        if normalized == f"principals/{username}":
            properties = f"<d:resourcetype><d:principal/></d:resourcetype><d:displayname>{escape(username)}</d:displayname><d:principal-URL><d:href>{escape(principal)}</d:href></d:principal-URL><card:addressbook-home-set><d:href>{escape(home)}</d:href></card:addressbook-home-set>"
            return _xml([(principal, properties)])
        if normalized == f"addressbooks/{username}":
            items = [(home, "<d:resourcetype><d:collection/></d:resourcetype><d:displayname>SimpleOffice Adressbuecher</d:displayname>")]
            if request.headers.get("Depth", "0") != "0":
                items.append((addressbook, _addressbook_properties()))
            return _xml(items)
        if normalized == f"addressbooks/{username}/default":
            return _xml([(addressbook, _addressbook_properties())])
        return Response("not found", 404)
    if request.method == "REPORT":
        if normalized != f"addressbooks/{username}/default":
            return Response("not found", 404)
        items = []
        for contact in store.contacts(username):
            href = base + contact["contact_id"] + ".vcf"
            card = escape(store.vcard(contact["contact_id"], username))
            items.append((href, f"<d:getetag>{_etag(contact)}</d:getetag><card:address-data content-type=\"text/vcard\" version=\"4.0\">{card}</card:address-data>"))
        return _xml(items)
    if normalized.endswith(".vcf"):
        contact_id = normalized.rsplit("/", 1)[-1][:-4]
        if request.method == "GET":
            try: card = store.vcard(contact_id, username); contact = store.get(contact_id, username)
            except ValueError: return Response("not found", 404)
            return Response(card, 200, {"Content-Type": "text/vcard; charset=utf-8", "ETag": _etag(contact)})
        if request.method == "PUT":
            try:
                existing = store.get(contact_id, username)
                created = False
            except ValueError:
                try:
                    store.get(contact_id)
                except ValueError:
                    existing = None
                    created = True
                else:
                    return Response("forbidden", 403)
            if existing is not None and not store.can_manage(contact_id, username):
                return Response("forbidden", 403)
            expected_updated_at = None
            create_only = request.headers.get("If-None-Match") == "*"
            if existing is not None:
                current_etag = _etag(existing)
                if create_only:
                    return _precondition_failed(current_etag)
                if request.headers.get("If-Match") and not _etag_matches(request.headers["If-Match"], current_etag):
                    return _precondition_failed(current_etag)
                if request.headers.get("If-Match"):
                    expected_updated_at = existing.get("updated_at", "")
            elif request.headers.get("If-Match"):
                return _precondition_failed()
            try:
                contact = store.conditional_upsert_vcard(
                    request.get_data(as_text=True),
                    f"carddav:{username}",
                    contact_id,
                    expected_updated_at=expected_updated_at,
                    create_only=create_only,
                )
            except ContactConflict as exc:
                return _precondition_failed(_etag(exc.contact) if exc.contact else "")
            except ValueError as exc:
                return Response(str(exc), 400)
            return Response("", 201 if created else 204, {"ETag": _etag(contact), "Location": base + contact["contact_id"] + ".vcf"})
        if request.method == "DELETE":
            try:
                existing = store.get(contact_id, username)
            except ValueError:
                return Response("not found", 404)
            if not store.can_manage(contact_id, username):
                return Response("forbidden", 403)
            current_etag = _etag(existing)
            if request.headers.get("If-Match") and not _etag_matches(request.headers["If-Match"], current_etag):
                return _precondition_failed(current_etag)
            expected_updated_at = existing.get("updated_at", "") if request.headers.get("If-Match") else None
            try: store.delete(contact_id, f"carddav:{username}", expected_updated_at)
            except ContactConflict as exc:
                return _precondition_failed(_etag(exc.contact) if exc.contact else "")
            except ValueError: return Response("not found", 404)
            return Response("", 204)
    return Response("not found", 404)
