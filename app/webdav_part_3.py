"""WebDAV implementation part 3 of 6."""
from __future__ import annotations

from .webdav_part_2 import *

def _if_condition_matches(condition: tuple[bool, str, str], state: dict, username: str, locks: dict) -> bool:
    negated, kind, supplied = condition
    matched = False
    if kind == "etag":
        matched = bool(state["etag"]) and not supplied.startswith("W/") and hmac.compare_digest(supplied, state["etag"])
    elif supplied.casefold().startswith("opaquelocktoken:"):
        matched = any(
            lock.get("username") == username and hmac.compare_digest(str(lock.get("token", "")), supplied)
            for _stored_key, lock in _locks_for(state["resource"], state["document"], locks)
        )
    elif supplied.casefold().startswith("urn:uuid:") and state["collection"]:
        if "sync_token" not in state:
            state["sync_token"] = _collection_sync_token(username, state["resource"])
        current = state["sync_token"]
        matched = hmac.compare_digest(current, supplied)
    return not matched if negated else matched


def _if_header_error(username: str, identity: dict) -> Response | None:
    """Evaluate RFC 4918 If lists and cache matching lock tokens by resource."""
    if getattr(g, "_webdav_if_checked", False):
        return None
    value = request.headers.get("If", "")
    g._webdav_if_checked = True
    g._webdav_if_tokens = {}
    g._webdav_if_etags = {}
    if not value:
        return None
    try:
        groups = _parse_if_header(value)
        locks = _active_locks().get("locks", {})
        for tag, lists in groups:
            state = _if_resource(tag, username, identity)
            successful = [
                conditions for conditions in lists
                if all(_if_condition_matches(condition, state, username, locks) for condition in conditions)
            ]
            if not successful:
                return Response("WebDAV If precondition failed", 412)
            tokens = {
                supplied for conditions in successful for negated, kind, supplied in conditions
                if not negated and kind == "token" and supplied.casefold().startswith("opaquelocktoken:")
            }
            if tokens:
                g._webdav_if_tokens.setdefault(state["key"], set()).update(tokens)
            etags = {
                supplied for conditions in successful for negated, kind, supplied in conditions
                if not negated and kind == "etag"
            }
            if etags:
                g._webdav_if_etags.setdefault(state["key"], set()).update(etags)
    except OverflowError as exc:
        return Response(str(exc), 413)
    except PermissionError:
        return Response("WebDAV If precondition targets an inaccessible resource", 412)
    except ValueError:
        return Response("invalid WebDAV If header or resource", 400)
    return None


def _request_token(key: str) -> str:
    tokens = getattr(g, "_webdav_if_tokens", {}).get(key, set())
    return next(iter(tokens)) if len(tokens) == 1 else ""


def _request_etag(key: str, current_etag: str) -> bool:
    """Return whether a successful DAV If list named this strong ETag."""
    return any(
        not supplied.startswith("W/") and hmac.compare_digest(supplied, current_etag)
        for supplied in getattr(g, "_webdav_if_etags", {}).get(key, set())
    )


def _unlock_token() -> tuple[str, Response | None]:
    value = request.headers.get("Lock-Token", "").strip()
    match = re.fullmatch(r"<(opaquelocktoken:[0-9a-fA-F-]+)>", value)
    if not match:
        return "", Response("UNLOCK requires exactly one valid Lock-Token header", 400)
    return match.group(1), None


def _save_lock(
    document_id: str,
    username: str,
    token: str,
    timeout_seconds: int,
    owner: str = "",
    *,
    href: str = "",
    depth: str = "0",
    resource: str = "",
) -> dict:
    path = _locks_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _active_locks()
        existing = payload["locks"].get(document_id)
        if existing and existing.get("token") != token:
            raise PermissionError("document is already locked")
        lock = {
            "token": token,
            "username": username,
            "owner": owner[:200],
            "href": href or str(existing.get("href", "") if existing else ""),
            "depth": depth,
            "resource": resource or str(existing.get("resource", "") if existing else ""),
            "created_at": existing.get("created_at", utc_now()) if existing else utc_now(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)).isoformat(),
        }
        payload["locks"][document_id] = lock
        atomic_json_write(path, payload)
        return lock


def _timeout_seconds() -> int:
    match = re.search(r"Second-(\d+)", request.headers.get("Timeout", ""), re.I)
    return max(60, min(int(match.group(1)) if match else 1800, 3600))


def _activelock_xml(lock: dict, href: str) -> str:
    seconds = max(0, int((datetime.fromisoformat(lock["expires_at"]) - datetime.now(timezone.utc)).total_seconds()))
    return f'''<d:activelock><d:locktype><d:write/></d:locktype><d:lockscope><d:exclusive/></d:lockscope><d:depth>{escape(str(lock.get("depth", "0")))}</d:depth><d:owner>{escape(lock.get("owner", ""))}</d:owner><d:timeout>Second-{seconds}</d:timeout><d:locktoken><d:href>{escape(lock["token"])}</d:href></d:locktoken><d:lockroot><d:href>{escape(str(lock.get("href", href)))}</d:href></d:lockroot></d:activelock>'''


def _lockdiscovery_xml(lock: dict | None, href: str) -> str:
    active = _activelock_xml(lock, href) if lock else ""
    return f'<d:lockdiscovery xmlns:d="DAV:">{active}</d:lockdiscovery>'


def _lock_xml(lock: dict, href: str) -> str:
    return f'''<?xml version="1.0" encoding="utf-8"?><d:prop xmlns:d="DAV:">{_lockdiscovery_xml(lock, href)}</d:prop>'''


def _record_lock_audit(action: str, username: str, resource: Path, lock: dict) -> None:
    relative = _store().relative(resource)
    _store().history.record(
        action,
        f"webdav:{username}",
        "webdav-locks",
        hashlib.sha256(f"{username}:{relative}".encode()).hexdigest(),
        {
            "resource": relative,
            "depth": str(lock.get("depth", "0")),
            "owner_present": bool(lock.get("owner")),
            "expires_at": str(lock.get("expires_at", "")),
            "changed_at": utc_now(),
            "actor": f"webdav:{username}",
        },
    )


def _parse_lock_body(body: bytes) -> str:
    root = _safe_xml_root(body, f"{{{DAV}}}lockinfo")
    allowed = {f"{{{DAV}}}lockscope", f"{{{DAV}}}locktype", f"{{{DAV}}}owner"}
    if any(child.tag not in allowed for child in root):
        raise ValueError("LOCK body contains an unsupported element")
    scopes = root.findall(f"{{{DAV}}}lockscope")
    types = root.findall(f"{{{DAV}}}locktype")
    owners = root.findall(f"{{{DAV}}}owner")
    if len(scopes) != 1 or len(types) != 1 or len(owners) > 1:
        raise ValueError("LOCK requires one lockscope and one locktype")
    if [child.tag for child in scopes[0]] != [f"{{{DAV}}}exclusive"]:
        raise ValueError("only exclusive WebDAV locks are supported")
    if [child.tag for child in types[0]] != [f"{{{DAV}}}write"]:
        raise ValueError("only write locks are supported")
    owner = "".join(owners[0].itertext()).strip() if owners else ""
    if len(owner.encode("utf-8")) > 1024:
        raise OverflowError("LOCK owner is too large")
    return owner


def _lock_request(username: str, resource: Path, document: dict | None, href: str) -> Response:
    """Create or explicitly refresh an RFC 4918 exclusive write lock."""
    body = request.get_data(cache=True)
    key = _lock_key(resource, document)
    existing = _lock_for(key)

    if not body.strip():
        token = _request_token(key)
        if not token or not existing or existing.get("token") != token or existing.get("username") != username:
            error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:lock-token-matches-request-uri/></d:error>'
            return Response(error, 412, mimetype="application/xml")
        lock = _save_lock(
            key, username, token, _timeout_seconds(), existing.get("owner", ""),
            href=str(existing.get("href", href)), depth=str(existing.get("depth", "0")),
            resource=_store().relative(resource),
        )
        _record_lock_audit("webdav_lock_refreshed", username, resource, lock)
        return Response(_lock_xml(lock, href), 200, {"Content-Type": "application/xml; charset=utf-8", "Cache-Control": "no-store"})

    depth = request.headers.get("Depth", "infinity").casefold()
    if depth not in {"0", "infinity"}:
        return Response("LOCK Depth must be 0 or infinity", 400)

    try:
        owner = _parse_lock_body(body)
    except OverflowError as exc:
        return Response(str(exc), 413)
    except PermissionError:
        error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:no-external-entities/></d:error>'
        return Response(error, 400, mimetype="application/xml")
    except ValueError as exc:
        return Response(str(exc), 400)
    effective_depth = depth if resource.is_dir() else "0"
    if _conflicting_locks(resource, document, effective_depth):
        error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:no-conflicting-lock/></d:error>'
        return Response(error, 423, mimetype="application/xml")
    if document is not None:
        try:
            _store()._require_document_editable(document)
        except ValueError as exc:
            return Response(str(exc), 423)

    token = f"opaquelocktoken:{uuid.uuid4()}"
    status = 200
    if not resource.exists():
        if not resource.parent.is_dir() or resource.parent.is_symlink():
            return Response("parent collection does not exist", 409)
        provisional_key = key
        try:
            _save_lock(
                provisional_key, username, token, _timeout_seconds(), owner,
                href=href, depth="0", resource=_store().relative(resource),
            )
            document = _store().create_document_at(
                _store().relative(resource), b"", f"webdav:{username}",
                max_bytes=int(current_app.config["MAX_CONTENT_LENGTH"]),
            )
        except (FileExistsError, ValueError) as exc:
            _release_lock(provisional_key)
            return Response(str(exc), 409)
        _release_lock(provisional_key)
        key = _lock_key(resource, document)
        _record_sync_changes(username, _store().relative(resource))
        status = 201
    try:
        lock = _save_lock(
            key, username, token, _timeout_seconds(), owner,
            href=href, depth=effective_depth, resource=_store().relative(resource),
        )
    except PermissionError:
        return Response("locked", 423)
    _record_lock_audit("webdav_lock_created", username, resource, lock)
    return Response(
        _lock_xml(lock, href), status,
        {"Content-Type": "application/xml; charset=utf-8", "Lock-Token": f"<{token}>", "Cache-Control": "no-store"},
    )


def _safe_xml_root(body: bytes, expected_tag: str) -> ElementTree.Element:
    """Parse bounded WebDAV XML without accepting entity declarations."""
    if len(body) > MAX_PROPERTY_BODY:
        raise OverflowError("WebDAV XML body is too large")
    upper = body.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise PermissionError("external and declared XML entities are not allowed")
    try:
        root = DefusedElementTree.fromstring(body)
    except (ElementTree.ParseError, DefusedXmlException) as exc:
        raise ValueError("invalid WebDAV XML") from exc
    if root.tag != expected_tag:
        raise ValueError("unexpected WebDAV XML root")
    if sum(1 for _ in root.iter()) > MAX_PROPERTY_NODES:
        raise OverflowError("WebDAV XML contains too many elements")
    return root


def _property_resource_key(username: str, resource: Path, document: dict | None) -> str:
    if document is not None:
        stable = f"document:{document['document_id']}"
    else:
        policy = _read_json(resource / POLICY_FILE, {})
        folder_id = str(policy.get("folder_id", "")).strip()
        stable = f"collection:{folder_id}" if folder_id else f"collection-path:{_store().relative(resource)}"
    return f"{username}:{stable}"


def _dead_properties(username: str, resource: Path, document: dict | None) -> dict[str, str]:
    key = _property_resource_key(username, resource, document)
    if request.method in {"PROPFIND", "SEARCH"}:
        if not hasattr(g, "_webdav_propfind_properties"):
            g._webdav_propfind_properties = _read_json(
                _properties_path(), {"resources": {}},
            ).get("resources", {})
        resources = g._webdav_propfind_properties
    else:
        resources = _read_json(_properties_path(), {"resources": {}}).get("resources", {})
    properties = resources.get(key, {}) if isinstance(resources, dict) else {}
    if not isinstance(properties, dict):
        return {}
    return {
        str(name): str(value) for name, value in properties.items()
        if isinstance(name, str) and isinstance(value, str)
    }


def _content_language(username: str, resource: Path, document: dict | None) -> str:
    serialized = _dead_properties(username, resource, document).get(f"{{{DAV}}}getcontentlanguage", "")
    if not serialized:
        return ""
    try:
        return (DefusedElementTree.fromstring(serialized).text or "").strip()
    except (ElementTree.ParseError, DefusedXmlException):
        return ""


def _xml_element(tag: str, text: str | None = None, child: ElementTree.Element | None = None) -> str:
    element = ElementTree.Element(tag)
    if text is not None:
        element.text = text
    if child is not None:
        element.append(child)
    return ElementTree.tostring(element, encoding="unicode", short_empty_elements=True)


def _rfc3339_timestamp(value: object) -> str:
    """Return a canonical UTC RFC 3339 value or leave unreliable legacy data undefined."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _resource_creationdate(
    resource: Path,
    document: dict | None,
    *,
    collection: bool,
) -> str:
    if collection:
        policy = _read_json(resource / POLICY_FILE, {})
        return _rfc3339_timestamp(policy.get("created_at"))
    return _rfc3339_timestamp((document or {}).get("first_seen_at"))


def _principal_url(username: str, *, collection: bool = False) -> str:
    return url_for(
        "webdav.principal_resource",
        username=username,
        principal_id="" if collection else "self",
    )


def _href_property(tag: str, href: str) -> str:
    element = ElementTree.Element(tag)
    ElementTree.SubElement(element, f"{{{DAV}}}href").text = href
    return ElementTree.tostring(element, encoding="unicode")


def _access_control_live_properties(*, collection: bool) -> dict[str, str]:
    """Expose the current credential's effective, read-only privilege view."""
    identity = getattr(g, "_webdav_identity", None)
    if not isinstance(identity, dict) or not identity.get("username"):
        return {}
    principal = _principal_url(identity["username"])
    principal_collection = _principal_url(identity["username"], collection=True)
    privileges = ["read", "read-current-user-privilege-set"]
    if identity.get("scope") == "write":
        privileges.extend(["write", "write-properties", "write-content", "unlock"])
        if collection:
            privileges.extend(["bind", "unbind"])
    privilege_set = ElementTree.Element(f"{{{DAV}}}current-user-privilege-set")
    for name in privileges:
        privilege = ElementTree.SubElement(privilege_set, f"{{{DAV}}}privilege")
        ElementTree.SubElement(privilege, f"{{{DAV}}}{name}")
    return {
        f"{{{DAV}}}owner": _href_property(f"{{{DAV}}}owner", principal),
        f"{{{DAV}}}current-user-principal": _href_property(
            f"{{{DAV}}}current-user-principal", principal,
        ),
        f"{{{DAV}}}principal-collection-set": _href_property(
            f"{{{DAV}}}principal-collection-set", principal_collection,
        ),
        f"{{{DAV}}}current-user-privilege-set": ElementTree.tostring(
            privilege_set, encoding="unicode",
        ),
    }


def _search_discovery_live_properties(*, collection: bool) -> dict[str, str]:
    """Advertise RFC 5323 only on hierarchical resources that execute SEARCH."""
    identity = getattr(g, "_webdav_identity", None)
    if not isinstance(identity, dict):
        return {}
    methods = ["OPTIONS", "PROPFIND", "SEARCH", "GET", "HEAD"]
    if collection:
        methods.insert(2, "REPORT")
    if identity.get("scope") == "write":
        methods[2:2] = ["PROPPATCH"]
        methods.extend(["PUT", "DELETE", "MKCOL", "COPY", "MOVE", "LOCK", "UNLOCK"])
    supported_methods = ElementTree.Element(f"{{{DAV}}}supported-method-set")
    for name in methods:
        ElementTree.SubElement(
            supported_methods, f"{{{DAV}}}supported-method", {"name": name},
        )
    grammars = ElementTree.Element(f"{{{DAV}}}supported-query-grammar-set")
    supported = ElementTree.SubElement(
        grammars, f"{{{DAV}}}supported-query-grammar",
    )
    grammar = ElementTree.SubElement(supported, f"{{{DAV}}}grammar")
    ElementTree.SubElement(grammar, f"{{{DAV}}}basicsearch")
    return {
        f"{{{DAV}}}supported-method-set": ElementTree.tostring(
            supported_methods, encoding="unicode",
        ),
        f"{{{DAV}}}supported-query-grammar-set": ElementTree.tostring(
            grammars, encoding="unicode",
        ),
    }


def _live_properties(
    display_name: str,
    *,
    collection: bool,
    document: dict | None,
    sync_token: str = "",
    quota: dict[str, int] | None = None,
    lock: dict | None = None,
    href: str = "",
    resource: Path | None = None,
    searchable: bool = False,
) -> dict[str, str]:
    supported = ElementTree.Element(f"{{{DAV}}}supportedlock")
    entry = ElementTree.SubElement(supported, f"{{{DAV}}}lockentry")
    scope = ElementTree.SubElement(entry, f"{{{DAV}}}lockscope")
    ElementTree.SubElement(scope, f"{{{DAV}}}exclusive")
    locktype = ElementTree.SubElement(entry, f"{{{DAV}}}locktype")
    ElementTree.SubElement(locktype, f"{{{DAV}}}write")
    values = {
        f"{{{DAV}}}displayname": _xml_element(f"{{{DAV}}}displayname", display_name),
        f"{{{DAV}}}supportedlock": ElementTree.tostring(supported, encoding="unicode"),
        f"{{{DAV}}}lockdiscovery": _lockdiscovery_xml(lock, href),
        f"{{{DAV}}}iscollection": _xml_element(
            f"{{{DAV}}}iscollection", "1" if collection else "0",
        ),
        f"{{{DAV}}}isFolder": _xml_element(
            f"{{{DAV}}}isFolder", "t" if collection else "f",
        ),
        f"{{{DAV}}}ishidden": _xml_element(
            f"{{{DAV}}}ishidden",
            "1" if resource is not None and resource.name.startswith(".") else "0",
        ),
        **_access_control_live_properties(collection=collection),
        **(_search_discovery_live_properties(collection=collection) if searchable else {}),
    }
    path = resource or (_document_path(document) if document else None)
    if path is not None:
        created_at = _resource_creationdate(path, document, collection=collection)
        if created_at:
            values[f"{{{DAV}}}creationdate"] = _xml_element(
                f"{{{DAV}}}creationdate", created_at,
            )
        stat = path.stat()
        values[f"{{{DAV}}}getlastmodified"] = _xml_element(
            f"{{{DAV}}}getlastmodified", formatdate(stat.st_mtime, usegmt=True),
        )
    if collection:
        resource_type = ElementTree.Element(f"{{{DAV}}}resourcetype")
        ElementTree.SubElement(resource_type, f"{{{DAV}}}collection")
        report_set = ElementTree.Element(f"{{{DAV}}}supported-report-set")
        supported_report = ElementTree.SubElement(report_set, f"{{{DAV}}}supported-report")
        report = ElementTree.SubElement(supported_report, f"{{{DAV}}}report")
        ElementTree.SubElement(report, f"{{{DAV}}}sync-collection")
        values.update({
            f"{{{DAV}}}resourcetype": ElementTree.tostring(resource_type, encoding="unicode"),
            f"{{{DAV}}}supported-report-set": ElementTree.tostring(report_set, encoding="unicode"),
        })
        if sync_token:
            values[f"{{{DAV}}}sync-token"] = _xml_element(f"{{{DAV}}}sync-token", sync_token)
        if quota is not None:
            values[f"{{{DAV}}}quota-available-bytes"] = _xml_element(
                f"{{{DAV}}}quota-available-bytes", str(quota["available"])
            )
            values[f"{{{DAV}}}quota-used-bytes"] = _xml_element(
                f"{{{DAV}}}quota-used-bytes", str(quota["used"])
            )
        return values
    if path is None:
        return values
    values.update({
        f"{{{DAV}}}resourcetype": _xml_element(f"{{{DAV}}}resourcetype"),
        f"{{{DAV}}}getcontentlength": _xml_element(f"{{{DAV}}}getcontentlength", str(stat.st_size)),
        f"{{{DAV}}}getcontenttype": _xml_element(f"{{{DAV}}}getcontenttype", mimetypes.guess_type(path.name)[0] or "application/octet-stream"),
        f"{{{DAV}}}getetag": _xml_element(f"{{{DAV}}}getetag", _etag(document or {})),
    })
    return values


def _parse_propfind(body: bytes) -> tuple[str, list[str]]:
    if not body.strip():
        return "allprop", []
    root = _safe_xml_root(body, f"{{{DAV}}}propfind")
    selectors = [child for child in root if child.tag in {f"{{{DAV}}}allprop", f"{{{DAV}}}propname", f"{{{DAV}}}prop"}]
    if len(selectors) != 1:
        raise ValueError("PROPFIND requires exactly one property selector")
    selector = selectors[0]
    include_nodes = root.findall(f"{{{DAV}}}include")
    allowed = {selector, *include_nodes}
    if len(include_nodes) > 1 or any(child not in allowed for child in root):
        raise ValueError("PROPFIND contains an unsupported instruction")
    if selector.tag != f"{{{DAV}}}allprop" and include_nodes:
        raise ValueError("DAV:include is only valid with DAV:allprop")
    if selector.tag == f"{{{DAV}}}prop":
        if any(child.attrib or list(child) or (child.text or "").strip() for child in selector):
            raise ValueError("PROPFIND property selectors must not contain values")
        requested = [child.tag for child in selector]
        return "prop", requested
    if selector.tag == f"{{{DAV}}}propname":
        if selector.attrib or list(selector) or (selector.text or "").strip():
            raise ValueError("DAV:propname must be empty")
        return "propname", []
    if selector.attrib or list(selector) or (selector.text or "").strip():
        raise ValueError("DAV:allprop must be empty")
    include = include_nodes[0] if include_nodes else None
    if include is not None and any(child.attrib or list(child) or (child.text or "").strip() for child in include):
        raise ValueError("DAV:include property selectors must not contain values")
    return "allprop", [child.tag for child in include] if include is not None else []


def _empty_property(tag: str) -> str:
    return ElementTree.tostring(ElementTree.Element(tag), encoding="unicode", short_empty_elements=True)


def _prop_response(
    href: str,
    display_name: str,
    *,
    collection: bool = False,
    document: dict | None = None,
    sync_token: str = "",
    username: str = "",
    resource: Path | None = None,
    query: tuple[str, list[str]] | None = None,
    searchable: bool = False,
) -> str:
    applicable = _locks_for(resource, document) if resource is not None else []
    active_lock = applicable[0][1] if applicable else None
    live = _live_properties(
        display_name,
        collection=collection,
        document=document,
        sync_token=sync_token,
        quota=_quota_state() if collection and username else None,
        lock=active_lock,
        href=href,
        resource=resource,
        searchable=searchable,
    )
    dead = _dead_properties(username, resource, document) if username and resource is not None else {}
    propstats = _property_propstats(live, dead, query)
    return f'<d:response><d:href>{escape(href)}</d:href>{propstats}</d:response>'


def _property_propstats(
    live: dict[str, str],
    dead: dict[str, str],
    query: tuple[str, list[str]] | None,
) -> str:
    mode, requested = query or ("allprop", [])
    available = {**live, **dead}
    if mode == "propname":
        successful = [_empty_property(tag) for tag in available]
        missing: list[str] = []
    elif mode == "prop":
        successful = [available[tag] for tag in requested if tag in available]
        missing = [_empty_property(tag) for tag in requested if tag not in available]
    else:
        # RFC 4918 allprop includes dead properties and the live properties in
        # that RFC. Extension properties such as sync-token require include.
        extensions = {
            f"{{{DAV}}}alternate-URI-set", f"{{{DAV}}}current-user-principal",
            f"{{{DAV}}}current-user-privilege-set", f"{{{DAV}}}group-membership",
            f"{{{DAV}}}owner", f"{{{DAV}}}principal-collection-set",
            f"{{{DAV}}}principal-URL",
            f"{{{DAV}}}quota-available-bytes", f"{{{DAV}}}quota-used-bytes",
            f"{{{DAV}}}sync-token", f"{{{DAV}}}supported-method-set",
            f"{{{DAV}}}supported-query-grammar-set", f"{{{DAV}}}supported-report-set",
        }
        selected_live = {tag: value for tag, value in live.items() if tag not in extensions}
        available = {**selected_live, **dead}
        for tag in requested:
            if tag in live:
                available[tag] = live[tag]
        successful = list(available.values())
        missing = [_empty_property(tag) for tag in requested if tag not in available]
    propstats = f'<d:propstat><d:prop>{"".join(successful)}</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>'
    if missing:
        propstats += f'<d:propstat><d:prop>{"".join(missing)}</d:prop><d:status>HTTP/1.1 404 Not Found</d:status></d:propstat>'
    return propstats


def _principal_prop_response(
    href: str,
    username: str,
    *,
    collection: bool,
    query: tuple[str, list[str]],
) -> str:
    resource_type = ElementTree.Element(f"{{{DAV}}}resourcetype")
    ElementTree.SubElement(
        resource_type, f"{{{DAV}}}{'collection' if collection else 'principal'}",
    )
    live = {
        f"{{{DAV}}}displayname": _xml_element(
            f"{{{DAV}}}displayname",
            "SimpleOffice Principals" if collection else username,
        ),
        f"{{{DAV}}}resourcetype": ElementTree.tostring(resource_type, encoding="unicode"),
        **_access_control_live_properties(collection=collection),
    }
    if not collection:
        live.update({
            f"{{{DAV}}}alternate-URI-set": _xml_element(f"{{{DAV}}}alternate-URI-set"),
            f"{{{DAV}}}principal-URL": _href_property(
                f"{{{DAV}}}principal-URL", _principal_url(username),
            ),
            f"{{{DAV}}}group-membership": _xml_element(f"{{{DAV}}}group-membership"),
        })
    propstats = _property_propstats(live, {}, query)
    return f'<d:response><d:href>{escape(href)}</d:href>{propstats}</d:response>'


def _propfind_members(resource: Path, depth: str, username: str = "") -> list[tuple[Path, bool, dict | None]]:
    """Return a deterministic, bounded snapshot of DAV-compliant descendants."""
    if depth == "0":
        return []
    members: list[tuple[Path, bool, dict | None]] = []
    pending: list[tuple[Path, int]] = [(resource, 0)]
    visited = 0
    while pending:
        parent, parent_depth = pending.pop()
        try:
            children = sorted(parent.iterdir(), key=lambda item: item.name.casefold())
        except OSError as exc:
            raise _PropfindLimitError("tree-changed", len(members), 0) from exc
        nested_collections: list[tuple[Path, int]] = []
        for child in children:
            if child.name in {CONTROL_DIR, HISTORY_DIR, POLICY_FILE} or child.is_symlink():
                continue
            if username and not _vfs().allows(username, child, "read"):
                continue
            visited += 1
            if visited > MAX_WEBDAV_COLLECTION_MEMBERS:
                raise _PropfindLimitError(
                    "member-count", visited, MAX_WEBDAV_COLLECTION_MEMBERS,
                )
            child_depth = parent_depth + 1
            if depth == "infinity" and child_depth > MAX_WEBDAV_COLLECTION_DEPTH:
                raise _PropfindLimitError(
                    "nesting-depth", child_depth, MAX_WEBDAV_COLLECTION_DEPTH,
                )
            try:
                if child.is_dir():
                    members.append((child, True, None))
                    if depth == "infinity":
                        nested_collections.append((child, child_depth))
                elif child.is_file():
                    try:
                        document = _tree_document(child)
                    except ValueError:
                        continue
                    members.append((child, False, document))
            except OSError as exc:
                raise _PropfindLimitError("tree-changed", len(members), 0) from exc
        pending.extend(reversed(nested_collections))
    return members


def _propfind_limit_response(
    username: str,
    resource: Path,
    error: _PropfindLimitError,
) -> Response:
    """Return a complete error instead of a truncated or ambiguous tree listing."""
    actor = f"webdav:{username}"
    relative = _store().relative(resource)
    _store().history.record(
        "webdav_propfind_limit_rejected",
        actor,
        "webdav-propfind",
        hashlib.sha256(f"{username}:{relative}:{error.reason}".encode()).hexdigest(),
        {
            "actor": actor,
            "resource": relative,
            "reason": error.reason,
            "observed": error.observed,
            "limit": error.limit,
            "rejected_at": utc_now(),
        },
    )
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<d:error xmlns:d="DAV:" xmlns:s="urn:simpleoffice:webdav">'
        f'<s:propfind-resource-limit reason="{error.reason}"/>'
        '</d:error>'
    )
    return Response(
        xml,
        507,
        {
            "Content-Type": "application/xml; charset=utf-8",
            "Cache-Control": "private, no-store",
            "X-SimpleOffice-Propfind-Limit": error.reason,
        },
    )


def _append_propfind_response(
    responses: list[str], response: str, current_size: int,
) -> int:
    """Bound memory while constructing a potentially property-heavy result."""
    size = current_size + len(response.encode("utf-8"))
    if size > MAX_PROPFIND_RESPONSE_BYTES:
        raise _PropfindLimitError("response-bytes", size, MAX_PROPFIND_RESPONSE_BYTES)
    responses.append(response)
    return size


def _propfind_multistatus(responses: list[str]) -> str:
    return PROPFIND_XML_PREFIX + "".join(responses) + PROPFIND_XML_SUFFIX


def _search_error_response(error: _SearchError) -> Response:
    condition = (
        f"<d:{error.condition}/>" if error.condition
        else '<s:invalid-search xmlns:s="urn:simpleoffice:webdav"/>'
    )
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<d:error xmlns:d="DAV:">{condition}</d:error>'
    )
    return Response(
        xml,
        error.status,
        {
            "Content-Type": "application/xml; charset=utf-8",
            "Cache-Control": "private, no-store",
            "Vary": "Authorization",
            "X-SimpleOffice-Search-Error": error.message,
        },
    )


def _search_property_tag(node: ElementTree.Element) -> str:
    if node.tag != f"{{{DAV}}}prop" or node.attrib or (node.text or "").strip():
        raise _SearchError(400, "property-operand-invalid")
    properties = list(node)
    if len(properties) != 1:
        raise _SearchError(400, "property-operand-count")
    selected = properties[0]
    if selected.attrib or list(selected) or (selected.text or "").strip():
        raise _SearchError(400, "property-selector-must-be-empty")
    return selected.tag


def _search_caseless(node: ElementTree.Element) -> bool:
    if any(name != "caseless" for name in node.attrib):
        raise _SearchError(400, "unsupported-search-attribute")
    value = node.attrib.get("caseless", "no")
    if value not in {"yes", "no"}:
        raise _SearchError(400, "caseless-must-be-yes-or-no")
    return value == "yes"

# Export private helpers too so later ordered parts see the same globals
# they had in the original single module.
__all__ = [name for name in globals() if not name.startswith("__")]
