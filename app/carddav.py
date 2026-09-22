"""Authenticated CardDAV address book with Thunderbird-compatible discovery and sync."""
from __future__ import annotations

import hashlib
import json
from urllib.parse import urlparse
from xml.etree import ElementTree as ET
from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException
from xml.sax.saxutils import escape

from flask import Blueprint, Response, current_app, request, url_for

from .contact_store import ContactConflict, ContactStore
from .vcard_utils import property_name as vcard_property_name, validate_single_vcard

bp = Blueprint("carddav", __name__)
DAV = "DAV:"
CARD = "urn:ietf:params:xml:ns:carddav"
CS = "http://calendarserver.org/ns/"


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
    responses = "".join(f"<d:response><d:href>{escape(href)}</d:href><d:propstat><d:prop>{props}</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>" for href, props in items)
    body = f'<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="{DAV}" xmlns:card="{CARD}" xmlns:cs="{CS}">{responses}</d:multistatus>'
    return Response(body, 207, content_type="application/xml; charset=utf-8")


def _etag(store: ContactStore, username: str, contact: dict) -> str:
    """Hash the representation actually visible to this CardDAV principal."""
    card = store.vcard(contact["contact_id"], username).encode("utf-8")
    return '"' + hashlib.sha256(card).hexdigest() + '"'


def _etag_matches(value: str, current: str) -> bool:
    return any(candidate.strip() in {"*", current} for candidate in value.split(","))


def _precondition_failed(current_etag: str = "") -> Response:
    return Response("CardDAV precondition failed", 412, {"ETag": current_etag} if current_etag else {})


def _privileges(writable: bool = True) -> str:
    values = ["<d:privilege><d:read/></d:privilege>"]
    if writable:
        values += ["<d:privilege><d:write/></d:privilege>", "<d:privilege><d:write-content/></d:privilege>", "<d:privilege><d:bind/></d:privilege>", "<d:privilege><d:unbind/></d:privilege>"]
    return "<d:current-user-privilege-set>" + "".join(values) + "</d:current-user-privilege-set>"


def _addressbook_properties(store: ContactStore | None = None, username: str = "") -> str:
    ctag = ""
    if store is not None and username:
        tokens = [
            f"{contact['contact_id']}:{_etag(store, username, contact)}"
            for contact in store.contacts(username)
        ]
        digest = hashlib.sha256("|".join(tokens).encode("utf-8")).hexdigest()
        ctag = f"<cs:getctag>{digest}</cs:getctag>"
    reports = "<d:supported-report-set><d:supported-report><d:report><card:addressbook-query/></d:report></d:supported-report><d:supported-report><d:report><card:addressbook-multiget/></d:report></d:supported-report></d:supported-report-set>"
    address_data = '<card:supported-address-data><card:address-data-type content-type="text/vcard" version="4.0"/></card:supported-address-data>'
    return f"<d:resourcetype><d:collection/><card:addressbook/></d:resourcetype><d:displayname>SimpleOffice Kontakte</d:displayname>{address_data}{reports}{ctag}{_privileges(True)}"


def _canonicalize_collection(path: str, username: str) -> str:
    normalized = path.strip("/")
    legacy, canonical = f"addressbooks/{username}/contacts", f"addressbooks/{username}/default"
    if normalized == legacy:
        return canonical
    if normalized.startswith(legacy + "/"):
        return canonical + normalized[len(legacy):]
    return normalized


def _diagnostics(store: ContactStore, username: str) -> dict:
    all_contacts, visible = store.contacts(), store.contacts(username)
    ownerless = sum(1 for c in all_contacts if not (str(c.get("owner", "")).strip() or store._principal(str(c.get("created_by", "")))))
    inaccessible = len(all_contacts) - len(visible)
    issues = []
    if not all_contacts: issues.append({"code": "no_contacts", "detail": "Im Kontaktbestand sind keine Kontakte gespeichert."})
    elif not visible: issues.append({"code": "no_visible_contacts", "detail": "Kontakte sind vorhanden, aber fuer diesen CardDAV-Benutzer nicht freigegeben."})
    elif inaccessible: issues.append({"code": "partially_visible", "detail": f"{inaccessible} Kontakt(e) sind fuer diesen CardDAV-Benutzer nicht sichtbar."})
    if ownerless: issues.append({"code": "owner_missing", "detail": f"{ownerless} Kontakt(e) haben keine auswertbare Eigentuemerk Zuordnung."})
    return {"status": "ok" if not issues else "warning", "authenticated_user": username, "canonical_collection": f"/carddav/addressbooks/{username}/default/", "legacy_collection": f"/carddav/addressbooks/{username}/contacts/", "contacts_total": len(all_contacts), "contacts_visible": len(visible), "contacts_inaccessible": inaccessible, "contacts_ownerless": ownerless, "issues": issues}


def _contact_item(store: ContactStore, username: str, contact: dict, with_card: bool = False) -> tuple[str, str]:
    href = f"/carddav/addressbooks/{username}/default/{contact['contact_id']}.vcf"
    props = f"<d:getetag>{_etag(store, username, contact)}</d:getetag><d:getcontenttype>text/vcard; charset=utf-8</d:getcontenttype>"
    if with_card:
        props += f'<card:address-data content-type="text/vcard" version="4.0">{escape(store.vcard(contact["contact_id"], username))}</card:address-data>'
    return href, props


def _vcard_properties(card: str) -> dict[str, list[tuple[str, dict[str, list[str]]]]]:
    lines, _version = validate_single_vcard(card)
    result: dict[str, list[tuple[str, dict[str, list[str]]]]] = {}
    for line in lines[2:-1]:
        header, separator, raw_value = line.partition(":")
        if not separator:
            continue
        name = vcard_property_name(line)
        params: dict[str, list[str]] = {}
        for token in header.split(";")[1:]:
            key, equals, value = token.partition("=")
            if equals:
                params.setdefault(key.strip().upper(), []).extend(
                    part.strip() for part in value.split(",") if part.strip()
                )
            elif token.strip():
                params.setdefault("TYPE", []).append(token.strip())
        result.setdefault(name, []).append(
            (ContactStore._unescape_vcard_text(raw_value), params)
        )
    return result


def _text_match(value: str, node: ET.Element) -> bool:
    collation = node.attrib.get("collation", "i;unicode-casemap")
    if collation not in {"i;unicode-casemap", "i;ascii-casemap"}:
        raise ValueError("unsupported CardDAV text-match collation")
    match_type = node.attrib.get("match-type", "contains")
    if match_type not in {"contains", "equals", "starts-with", "ends-with"}:
        raise ValueError("unsupported CardDAV text-match type")
    haystack = str(value).casefold()
    needle = str(node.text or "").casefold()
    matched = {
        "contains": needle in haystack,
        "equals": haystack == needle,
        "starts-with": haystack.startswith(needle),
        "ends-with": haystack.endswith(needle),
    }[match_type]
    if node.attrib.get("negate-condition", "no") == "yes":
        matched = not matched
    elif node.attrib.get("negate-condition", "no") != "no":
        raise ValueError("invalid CardDAV negate-condition")
    return matched


def _parameter_matches(params: dict[str, list[str]], node: ET.Element) -> bool:
    name = str(node.attrib.get("name", "")).strip().upper()
    if not name:
        raise ValueError("CardDAV param-filter requires a name")
    values = params.get(name, [])
    children = list(node)
    if not children:
        return bool(values)
    if len(children) == 1 and children[0].tag == f"{{{CARD}}}is-not-defined":
        return not values
    for child in children:
        if child.tag != f"{{{CARD}}}text-match":
            raise ValueError("unsupported CardDAV param-filter child")
    return bool(values) and all(_text_match(value, child) for child in children for value in values)


def _property_matches(
    entries: list[tuple[str, dict[str, list[str]]]],
    node: ET.Element,
) -> bool:
    children = list(node)
    if len(children) == 1 and children[0].tag == f"{{{CARD}}}is-not-defined":
        return not entries
    if not entries:
        return False
    allowed = {f"{{{CARD}}}text-match", f"{{{CARD}}}param-filter"}
    if any(child.tag not in allowed for child in children):
        raise ValueError("unsupported CardDAV prop-filter child")
    mode = node.attrib.get("test", "anyof")
    if mode not in {"anyof", "allof"}:
        raise ValueError("invalid CardDAV prop-filter test")
    for value, params in entries:
        checks = [
            _text_match(value, child)
            if child.tag == f"{{{CARD}}}text-match"
            else _parameter_matches(params, child)
            for child in children
        ]
        if not checks:
            return True
        if (mode == "allof" and all(checks)) or (mode == "anyof" and any(checks)):
            return True
    return False


def _query_matches(store: ContactStore, username: str, contact: dict, filter_node: ET.Element | None) -> bool:
    if filter_node is None:
        return True
    mode = filter_node.attrib.get("test", "anyof")
    if mode not in {"anyof", "allof"}:
        raise ValueError("invalid CardDAV filter test")
    props = _vcard_properties(store.vcard(contact["contact_id"], username))
    checks: list[bool] = []
    for child in list(filter_node):
        if child.tag != f"{{{CARD}}}prop-filter":
            raise ValueError("unsupported CardDAV addressbook-query filter")
        name = str(child.attrib.get("name", "")).strip().upper()
        if not name:
            raise ValueError("CardDAV prop-filter requires a name")
        checks.append(_property_matches(props.get(name, []), child))
    if not checks:
        return True
    return all(checks) if mode == "allof" else any(checks)


def _report_contacts(store: ContactStore, username: str) -> list[dict]:
    contacts = store.contacts(username)
    raw = request.get_data(cache=True)
    if not raw:
        return contacts
    try:
        root = DefusedET.fromstring(raw)
    except (ET.ParseError, DefusedXmlException) as exc:
        raise ValueError("invalid CardDAV REPORT XML") from exc
    if root.tag == f"{{{CARD}}}addressbook-multiget":
        wanted = set()
        for node in root.findall(f".//{{{DAV}}}href"):
            path = urlparse(node.text or "").path.rstrip("/")
            if path.endswith(".vcf"):
                wanted.add(path.rsplit("/", 1)[-1][:-4])
        return [contact for contact in contacts if contact.get("contact_id") in wanted]
    if root.tag == f"{{{CARD}}}addressbook-query":
        filter_node = root.find(f"{{{CARD}}}filter")
        return [
            contact for contact in contacts
            if _query_matches(store, username, contact, filter_node)
        ]
    raise ValueError("unsupported CardDAV REPORT")


@bp.route("/.well-known/carddav", methods=["OPTIONS", "PROPFIND", "GET"])
def well_known():
    return Response("", 307, {"Location": url_for("carddav.endpoint", path="", _external=True), "Cache-Control": "public, max-age=3600"})


@bp.route("/carddav/", defaults={"path": ""}, methods=["OPTIONS", "PROPFIND", "REPORT", "GET", "PUT", "DELETE"])
@bp.route("/carddav/<path:path>", methods=["OPTIONS", "PROPFIND", "REPORT", "GET", "PUT", "DELETE"])
def endpoint(path: str):
    username = _auth()
    if username is None: return _unauthorized()
    store = _store(); base = f"/carddav/addressbooks/{username}/default/"
    if request.method == "OPTIONS": return Response("", 204, {"DAV": "1, addressbook", "Allow": "OPTIONS, PROPFIND, REPORT, GET, PUT, DELETE"})
    normalized = _canonicalize_collection(path, username)
    if normalized == "diagnostics" and request.method == "GET": return Response(json.dumps(_diagnostics(store, username), ensure_ascii=False, indent=2), 200, content_type="application/json; charset=utf-8")
    if normalized.startswith("addressbooks/") and normalized != f"addressbooks/{username}" and not normalized.startswith(f"addressbooks/{username}/"): return Response("not found", 404)
    if normalized.startswith("principals/") and normalized != f"principals/{username}": return Response("not found", 404)

    if request.method == "PROPFIND":
        if normalized.endswith(".vcf"):
            cid = normalized.rsplit("/",1)[-1][:-4]
            try: contact = store.get(cid, username)
            except ValueError: return Response("not found",404)
            href, props = _contact_item(store, username, contact)
            return _xml([(href, props + _privileges(store.can_manage(cid, username)))])
        principal = url_for("carddav.endpoint", path=f"principals/{username}/", _external=True)
        home = url_for("carddav.endpoint", path=f"addressbooks/{username}/", _external=True)
        addressbook = url_for("carddav.endpoint", path=f"addressbooks/{username}/default/", _external=True)
        if not normalized: return _xml([(request.url, f"<d:resourcetype><d:collection/></d:resourcetype><d:current-user-principal><d:href>{escape(principal)}</d:href></d:current-user-principal>")])
        if normalized == f"principals/{username}": return _xml([(principal, f"<d:resourcetype><d:principal/></d:resourcetype><d:displayname>{escape(username)}</d:displayname><d:principal-URL><d:href>{escape(principal)}</d:href></d:principal-URL><card:addressbook-home-set><d:href>{escape(home)}</d:href></card:addressbook-home-set>")])
        if normalized == f"addressbooks/{username}":
            items=[(home,"<d:resourcetype><d:collection/></d:resourcetype><d:displayname>SimpleOffice Adressbuecher</d:displayname>")]
            if request.headers.get("Depth","0") != "0": items.append((addressbook,_addressbook_properties(store, username)))
            return _xml(items)
        if normalized == f"addressbooks/{username}/default":
            items=[(addressbook,_addressbook_properties(store, username))]
            if request.headers.get("Depth","0") != "0": items.extend(_contact_item(store, username, c) for c in store.contacts(username))
            return _xml(items)
        return Response("not found",404)

    if request.method == "REPORT":
        if normalized != f"addressbooks/{username}/default": return Response("not found",404)
        try:
            contacts = _report_contacts(store, username)
        except ValueError as exc:
            return Response(str(exc), 400)
        return _xml([_contact_item(store, username, c, True) for c in contacts])

    if normalized.endswith(".vcf"):
        cid=normalized.rsplit("/",1)[-1][:-4]
        if request.method == "GET":
            try: card=store.vcard(cid,username); contact=store.get(cid,username)
            except ValueError: return Response("not found",404)
            return Response(card,200,{"Content-Type":"text/vcard; charset=utf-8","ETag":_etag(store,username,contact),"X-Content-Type-Options":"nosniff","Content-Security-Policy":"sandbox"})
        if request.method == "PUT":
            try: existing=store.get(cid,username); created=False
            except ValueError:
                try: store.get(cid)
                except ValueError: existing=None; created=True
                else: return Response("forbidden",403)
            if existing is not None and not store.can_manage(cid,username): return Response("forbidden",403)
            expected=None; create_only=request.headers.get("If-None-Match")=="*"
            if existing is not None:
                current=_etag(store,username,existing)
                if create_only: return _precondition_failed(current)
                if request.headers.get("If-Match") and not _etag_matches(request.headers["If-Match"],current): return _precondition_failed(current)
                if request.headers.get("If-Match"): expected=existing.get("updated_at","")
            elif request.headers.get("If-Match"): return _precondition_failed()
            try: contact=store.conditional_upsert_vcard(request.get_data(as_text=True),f"carddav:{username}",cid,expected_updated_at=expected,create_only=create_only)
            except ContactConflict as exc: return _precondition_failed(_etag(store,username,exc.contact) if exc.contact else "")
            except ValueError as exc: return Response(str(exc),400)
            return Response("",201 if created else 204,{"ETag":_etag(contact),"Location":base+contact["contact_id"]+".vcf"})
        if request.method == "DELETE":
            try: existing=store.get(cid,username)
            except ValueError: return Response("not found",404)
            if not store.can_manage(cid,username): return Response("forbidden",403)
            current=_etag(existing)
            if request.headers.get("If-Match") and not _etag_matches(request.headers["If-Match"],current): return _precondition_failed(current)
            try: store.delete(cid,f"carddav:{username}",existing.get("updated_at","") if request.headers.get("If-Match") else None)
            except ContactConflict as exc: return _precondition_failed(_etag(exc.contact) if exc.contact else "")
            except ValueError: return Response("not found",404)
            return Response("",204)
    return Response("not found",404)
