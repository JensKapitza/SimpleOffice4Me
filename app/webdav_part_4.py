"""WebDAV implementation part 4 of 6."""
from __future__ import annotations

from .webdav_part_3 import *

@functools.lru_cache(maxsize=128)
def _search_like_regex(pattern: str, caseless: bool) -> str:
    value = unicodedata.normalize("NFC", pattern.casefold() if caseless else pattern)
    parts: list[str] = []
    position = 0
    while position < len(value):
        character = value[position]
        if character == "%":
            parts.append(".*")
        elif character == "_":
            parts.append(".")
        elif character == "\\":
            position += 1
            if position >= len(value) or value[position] not in {"%", "_", "\\"}:
                raise _SearchError(422, "invalid-like-escape")
            parts.append(re.escape(value[position]))
        else:
            parts.append(re.escape(character))
        position += 1
    return "".join(parts)


def _validate_search_operator(
    node: ElementTree.Element, *, depth: int = 1, count: list[int] | None = None,
) -> int:
    if depth > MAX_SEARCH_EXPRESSION_DEPTH:
        raise _SearchError(422, "search-expression-too-deep")
    counter = count if count is not None else [0]
    counter[0] += 1
    if counter[0] > MAX_SEARCH_OPERATORS:
        raise _SearchError(422, "too-many-search-operators")
    logical = {
        f"{{{DAV}}}and": (1, None),
        f"{{{DAV}}}or": (1, None),
        f"{{{DAV}}}not": (1, 1),
    }
    comparisons = {
        f"{{{DAV}}}eq", f"{{{DAV}}}lt", f"{{{DAV}}}lte",
        f"{{{DAV}}}gt", f"{{{DAV}}}gte", f"{{{DAV}}}like",
    }
    if node.tag in logical:
        if node.attrib or (node.text or "").strip():
            raise _SearchError(400, "logical-operator-invalid")
        minimum, maximum = logical[node.tag]
        children = list(node)
        if len(children) < minimum or (maximum is not None and len(children) > maximum):
            raise _SearchError(400, "logical-operand-count")
        for child in children:
            _validate_search_operator(child, depth=depth + 1, count=counter)
        return counter[0]
    if node.tag in comparisons:
        caseless = _search_caseless(node)
        children = list(node)
        if len(children) != 2 or children[1].tag != f"{{{DAV}}}literal":
            raise _SearchError(422, "unsupported-search-operand")
        _search_property_tag(children[0])
        literal = children[1]
        if literal.attrib or list(literal):
            raise _SearchError(422, "unsupported-search-literal")
        value = literal.text or ""
        if len(value.encode("utf-8")) > MAX_PROPERTY_VALUE:
            raise _SearchError(413, "search-literal-too-large")
        if node.tag == f"{{{DAV}}}like":
            _search_like_regex(value, caseless)
        return counter[0]
    if node.tag == f"{{{DAV}}}is-collection":
        if node.attrib or list(node) or (node.text or "").strip():
            raise _SearchError(400, "is-collection-must-be-empty")
        return counter[0]
    if node.tag == f"{{{DAV}}}is-defined":
        if node.attrib or (node.text or "").strip() or len(node) != 1:
            raise _SearchError(400, "is-defined-invalid")
        _search_property_tag(node[0])
        return counter[0]
    raise _SearchError(422, "search-operator-not-supported")


def _parse_search(body: bytes) -> dict:
    try:
        root = _safe_xml_root(body, f"{{{DAV}}}searchrequest")
    except OverflowError:
        raise
    except PermissionError:
        raise
    except ValueError as exc:
        raise _SearchError(400, "invalid-search-xml") from exc
    if root.attrib or (root.text or "").strip() or len(root) != 1:
        raise _SearchError(400, "searchrequest-requires-one-grammar")
    basic = root[0]
    if basic.tag != f"{{{DAV}}}basicsearch":
        raise _SearchError(422, "search-grammar-not-supported", "search-grammar-supported")
    expected = [
        f"{{{DAV}}}select", f"{{{DAV}}}from", f"{{{DAV}}}where",
        f"{{{DAV}}}orderby", f"{{{DAV}}}limit",
    ]
    children = list(basic)
    positions = [expected.index(child.tag) if child.tag in expected else -1 for child in children]
    if (
        basic.attrib or (basic.text or "").strip()
        or len(children) < 2 or positions[:2] != [0, 1]
        or -1 in positions or positions != sorted(set(positions))
    ):
        raise _SearchError(400, "basicsearch-structure-invalid")
    sections = {child.tag: child for child in children}

    select = sections[f"{{{DAV}}}select"]
    if select.attrib or (select.text or "").strip() or len(select) != 1:
        raise _SearchError(400, "select-requires-one-selector")
    selector = select[0]
    if selector.tag == f"{{{DAV}}}allprop":
        if selector.attrib or list(selector) or (selector.text or "").strip():
            raise _SearchError(400, "allprop-must-be-empty")
        query = ("allprop", [])
    elif selector.tag == f"{{{DAV}}}prop":
        if selector.attrib or (selector.text or "").strip():
            raise _SearchError(400, "select-prop-invalid")
        requested = []
        for property_node in selector:
            if property_node.attrib or list(property_node) or (property_node.text or "").strip():
                raise _SearchError(400, "property-selector-must-be-empty")
            requested.append(property_node.tag)
        if not requested or len(requested) > MAX_PROPERTY_COUNT:
            raise _SearchError(413, "selected-property-count-invalid")
        query = ("prop", requested)
    else:
        raise _SearchError(400, "select-grammar-invalid")

    from_node = sections[f"{{{DAV}}}from"]
    scopes = list(from_node)
    if from_node.attrib or (from_node.text or "").strip() or not scopes:
        raise _SearchError(400, "search-scope-required")
    if len(scopes) != 1:
        raise _SearchError(422, "multiple-search-scopes-not-supported", "search-multiple-scope-supported")
    scope = scopes[0]
    if scope.tag != f"{{{DAV}}}scope" or scope.attrib:
        raise _SearchError(400, "search-scope-invalid")
    scope_children = list(scope)
    if [child.tag for child in scope_children] != [f"{{{DAV}}}href", f"{{{DAV}}}depth"]:
        raise _SearchError(409, "search-scope-invalid", "search-scope-valid")
    href_node, depth_node = scope_children
    if any(node.attrib or list(node) for node in (href_node, depth_node)):
        raise _SearchError(400, "search-scope-value-invalid")
    scope_href = (href_node.text or "").strip()
    scope_depth = (depth_node.text or "").strip().casefold()
    if not scope_href or len(scope_href.encode("utf-8")) > 2048:
        raise _SearchError(409, "search-scope-invalid", "search-scope-valid")
    if scope_depth not in {"0", "1", "infinity"}:
        raise _SearchError(409, "search-depth-invalid", "search-scope-valid")

    where = sections.get(f"{{{DAV}}}where")
    operator = None
    operator_count = 0
    if where is not None:
        if where.attrib or (where.text or "").strip() or len(where) != 1:
            raise _SearchError(400, "where-requires-one-operator")
        operator = where[0]
        operator_count = _validate_search_operator(operator)

    order_by: list[tuple[str, bool, bool]] = []
    orderby = sections.get(f"{{{DAV}}}orderby")
    if orderby is not None:
        orders = list(orderby)
        if orderby.attrib or (orderby.text or "").strip() or not orders:
            raise _SearchError(400, "orderby-invalid")
        if len(orders) > MAX_SEARCH_ORDERS:
            raise _SearchError(422, "too-many-sort-orders")
        for order in orders:
            if order.tag != f"{{{DAV}}}order" or (order.text or "").strip():
                raise _SearchError(400, "order-invalid")
            caseless = _search_caseless(order)
            operands = list(order)
            if len(operands) not in {1, 2}:
                raise _SearchError(400, "order-operand-count")
            property_tag = _search_property_tag(operands[0])
            descending = False
            if len(operands) == 2:
                direction = operands[1]
                if direction.tag not in {f"{{{DAV}}}ascending", f"{{{DAV}}}descending"} or direction.attrib or list(direction) or (direction.text or "").strip():
                    raise _SearchError(400, "order-direction-invalid")
                descending = direction.tag == f"{{{DAV}}}descending"
            order_by.append((property_tag, descending, caseless))

    result_limit = None
    limit = sections.get(f"{{{DAV}}}limit")
    if limit is not None:
        if limit.attrib or (limit.text or "").strip() or len(limit) != 1 or limit[0].tag != f"{{{DAV}}}nresults" or limit[0].attrib or list(limit[0]):
            raise _SearchError(400, "search-limit-invalid")
        value = (limit[0].text or "").strip()
        if not value.isdigit() or len(value) > 9:
            raise _SearchError(400, "search-limit-invalid")
        result_limit = int(value)
    return {
        "query": query,
        "scope_href": scope_href,
        "scope_depth": scope_depth,
        "operator": operator,
        "operator_count": operator_count,
        "order_by": order_by,
        "limit": result_limit,
    }


def _resolve_search_scope(
    username: str, identity: dict, arbiter: Path, scope_href: str,
) -> tuple[Path, bool, dict | None]:
    parsed = urlsplit(urljoin(request.url, scope_href))
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.netloc.casefold() != request.host.casefold()
        or parsed.username or parsed.password or parsed.query or parsed.fragment
    ):
        raise _SearchError(409, "search-scope-outside-server", "search-scope-valid")
    path = unquote(parsed.path).rstrip("/")
    prefix = unquote(_tree_url(username)).rstrip("/")
    if path != prefix and not path.startswith(prefix + "/"):
        raise _SearchError(409, "search-scope-outside-user-tree", "search-scope-valid")
    relative = path[len(prefix):].strip("/")
    try:
        resource = _tree_path(relative)
    except ValueError as exc:
        raise _SearchError(409, "search-scope-invalid", "search-scope-valid") from exc
    if not _credential_allows_path(identity, resource):
        raise _SearchError(409, "search-scope-outside-credential", "search-scope-valid")
    if resource != arbiter and arbiter not in resource.parents:
        raise _SearchError(409, "search-scope-outside-arbiter", "search-scope-valid")
    collection = resource.is_dir() and not resource.is_symlink()
    document = None
    if resource.is_file() and not resource.is_symlink():
        try:
            document = _tree_document(resource)
        except ValueError as exc:
            raise _SearchError(409, "search-scope-invalid", "search-scope-valid") from exc
    elif not collection:
        raise _SearchError(409, "search-scope-invalid", "search-scope-valid")
    return resource, collection, document


def _search_properties(
    username: str, resource: Path, document: dict | None, *, collection: bool,
) -> dict[str, str]:
    href = _tree_url(username, _store().relative(resource), collection=collection)
    applicable = _locks_for(resource, document)
    live = _live_properties(
        resource.name if resource != _store().root else "SimpleOffice Dokumente",
        collection=collection,
        document=document,
        quota=_quota_state() if collection else None,
        lock=applicable[0][1] if applicable else None,
        href=href,
        resource=resource,
        searchable=True,
    )
    return {**live, **_dead_properties(username, resource, document)}


def _search_simple_value(properties: dict[str, str], tag: str):
    serialized = properties.get(tag)
    if serialized is None:
        return None
    try:
        element = DefusedElementTree.fromstring(serialized)
    except (ElementTree.ParseError, DefusedXmlException):
        return None
    if list(element):
        return None
    value = element.text or ""
    if tag == f"{{{DAV}}}getcontentlength":
        try:
            return int(value)
        except ValueError:
            return None
    if tag in {f"{{{DAV}}}creationdate", f"{{{DAV}}}getlastmodified"}:
        try:
            parsed = (
                parsedate_to_datetime(value)
                if "," in value else datetime.fromisoformat(value.replace("Z", "+00:00"))
            )
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None
    return unicodedata.normalize("NFC", value)


def _search_literal_value(tag: str, value: str):
    if tag == f"{{{DAV}}}getcontentlength":
        try:
            return int(value)
        except ValueError:
            return None
    if tag in {f"{{{DAV}}}creationdate", f"{{{DAV}}}getlastmodified"}:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None
    return unicodedata.normalize("NFC", value)


def _search_evaluate(
    node: ElementTree.Element,
    properties: dict[str, str],
    *,
    collection: bool,
) -> bool | None:
    if node.tag in {f"{{{DAV}}}and", f"{{{DAV}}}or", f"{{{DAV}}}not"}:
        values = [
            _search_evaluate(child, properties, collection=collection)
            for child in node
        ]
        if node.tag == f"{{{DAV}}}not":
            return None if values[0] is None else not values[0]
        if node.tag == f"{{{DAV}}}and":
            return False if False in values else True if all(value is True for value in values) else None
        return True if True in values else False if all(value is False for value in values) else None
    if node.tag == f"{{{DAV}}}is-collection":
        return collection
    if node.tag == f"{{{DAV}}}is-defined":
        return _search_property_tag(node[0]) in properties
    property_tag = _search_property_tag(node[0])
    actual = _search_simple_value(properties, property_tag)
    if actual is None:
        return None
    literal = node[1].text or ""
    caseless = node.attrib.get("caseless", "no") == "yes"
    if node.tag == f"{{{DAV}}}like":
        if not isinstance(actual, str):
            return None
        candidate = actual.casefold() if caseless else actual
        return re.fullmatch(
            _search_like_regex(literal, caseless), candidate, flags=re.DOTALL,
        ) is not None
    expected = _search_literal_value(property_tag, literal)
    if expected is None or type(actual) is not type(expected):
        return None
    if caseless and isinstance(actual, str):
        actual, expected = actual.casefold(), expected.casefold()
    if node.tag == f"{{{DAV}}}eq":
        return actual == expected
    if node.tag == f"{{{DAV}}}lt":
        return actual < expected
    if node.tag == f"{{{DAV}}}lte":
        return actual <= expected
    if node.tag == f"{{{DAV}}}gt":
        return actual > expected
    return actual >= expected


def _search_order_compare(left: dict, right: dict, order_by: list[tuple[str, bool, bool]]) -> int:
    for tag, descending, caseless in order_by:
        first = _search_simple_value(left["properties"], tag)
        second = _search_simple_value(right["properties"], tag)
        if caseless:
            first = first.casefold() if isinstance(first, str) else first
            second = second.casefold() if isinstance(second, str) else second
        if first is None and second is None:
            continue
        if first is None:
            result = -1
        elif second is None:
            result = 1
        elif type(first) is not type(second):
            result = (str(first) > str(second)) - (str(first) < str(second))
        else:
            result = (first > second) - (first < second)
        if result:
            return -result if descending else result
    return (left["href"] > right["href"]) - (left["href"] < right["href"])


def _record_search_audit(
    username: str,
    scope: Path,
    *,
    action: str,
    depth: str,
    scanned: int,
    matched: int,
    operators: int,
    client_limit: int | None,
    reason: str = "",
) -> None:
    at = utc_now()
    details = {
        "actor": f"webdav:{username}",
        "scope": _store().relative(scope),
        "depth": depth,
        "scanned": scanned,
        "matched": matched,
        "operators": operators,
        "client_limit": client_limit,
        "reason": reason,
        "at": at,
    }
    _store().history.record(
        action,
        f"webdav:{username}",
        "webdav-search",
        hashlib.sha256(f"{username}:{at}:{uuid.uuid4()}".encode()).hexdigest(),
        details,
    )


def _search_limit_response(
    username: str,
    scope: Path,
    *,
    depth: str,
    scanned: int,
    matched: int,
    operators: int,
    client_limit: int | None,
    reason: str,
) -> Response:
    _record_search_audit(
        username, scope, action="webdav_search_limit_rejected", depth=depth,
        scanned=scanned, matched=matched, operators=operators,
        client_limit=client_limit, reason=reason,
    )
    href = _tree_url(username, _store().relative(scope), collection=scope.is_dir())
    response = (
        f'<d:response><d:href>{escape(href)}</d:href>'
        '<d:status>HTTP/1.1 507 Insufficient Storage</d:status></d:response>'
    )
    return Response(
        _propfind_multistatus([response]),
        207,
        {
            "Content-Type": "application/xml; charset=utf-8",
            "Cache-Control": "private, no-store",
            "Vary": "Authorization",
            "X-SimpleOffice-Search-Limit": reason,
        },
    )


def _search_response(
    username: str,
    identity: dict,
    arbiter: Path,
) -> Response:
    if request.mimetype not in {"application/xml", "text/xml"}:
        return _search_error_response(
            _SearchError(415, "search-content-type-not-supported"),
        )
    try:
        query = _parse_search(request.get_data(cache=True))
        scope, scope_collection, scope_document = _resolve_search_scope(
            username, identity, arbiter, query["scope_href"],
        )
    except OverflowError as exc:
        return _search_error_response(_SearchError(413, str(exc)))
    except PermissionError:
        return _search_error_response(_SearchError(400, "xml-entities-not-allowed"))
    except _SearchError as exc:
        return _search_error_response(exc)

    mutation_lock = exclusive_file_lock(_sync_path().with_suffix(".mutation.lock"))
    mutation_lock.__enter__()
    g._webdav_mutation_lock = mutation_lock
    scope_collection = scope.is_dir() and not scope.is_symlink()
    if scope.is_file() and not scope.is_symlink():
        try:
            scope_document = _tree_document(scope)
        except ValueError:
            return _search_error_response(
                _SearchError(409, "search-scope-changed", "search-scope-valid"),
            )
    elif not scope_collection:
        return _search_error_response(
            _SearchError(409, "search-scope-changed", "search-scope-valid"),
        )
    effective_depth = query["scope_depth"] if scope_collection else "0"
    candidates = [(scope, scope_collection, scope_document)]
    try:
        candidates.extend(_propfind_members(scope, effective_depth, username))
    except _PropfindLimitError as exc:
        return _search_limit_response(
            username, scope, depth=effective_depth, scanned=exc.observed,
            matched=0, operators=query["operator_count"],
            client_limit=query["limit"], reason=exc.reason,
        )

    matches: list[dict] = []
    for resource, collection, document in candidates:
        properties = _search_properties(
            username, resource, document, collection=collection,
        )
        if query["operator"] is not None and _search_evaluate(
            query["operator"], properties, collection=collection,
        ) is not True:
            continue
        matches.append({
            "resource": resource,
            "collection": collection,
            "document": document,
            "properties": properties,
            "href": _tree_url(
                username, _store().relative(resource), collection=collection,
            ),
        })
    if query["order_by"]:
        matches.sort(key=functools.cmp_to_key(
            lambda left, right: _search_order_compare(left, right, query["order_by"]),
        ))
    else:
        matches.sort(key=lambda item: item["href"].casefold())
    total_matches = len(matches)
    client_limit = query["limit"]
    if total_matches > MAX_SEARCH_RESULTS and (
        client_limit is None or client_limit > MAX_SEARCH_RESULTS
    ):
        return _search_limit_response(
            username, scope, depth=effective_depth, scanned=len(candidates),
            matched=total_matches, operators=query["operator_count"],
            client_limit=client_limit, reason="result-count",
        )
    if client_limit is not None:
        matches = matches[:client_limit]

    responses: list[str] = []
    response_size = len((PROPFIND_XML_PREFIX + PROPFIND_XML_SUFFIX).encode("utf-8"))
    try:
        for match in matches:
            response_size = _append_propfind_response(
                responses,
                _prop_response(
                    match["href"],
                    match["resource"].name if match["resource"] != _store().root else "SimpleOffice Dokumente",
                    collection=match["collection"],
                    document=match["document"],
                    username=username,
                    resource=match["resource"],
                    query=query["query"],
                    searchable=True,
                ),
                response_size,
            )
    except _PropfindLimitError as exc:
        return _search_limit_response(
            username, scope, depth=effective_depth, scanned=len(candidates),
            matched=total_matches, operators=query["operator_count"],
            client_limit=client_limit, reason=exc.reason,
        )
    _record_search_audit(
        username, scope, action="webdav_search_executed", depth=effective_depth,
        scanned=len(candidates), matched=total_matches,
        operators=query["operator_count"], client_limit=client_limit,
    )
    return Response(
        _propfind_multistatus(responses),
        207,
        {
            "Content-Type": "application/xml; charset=utf-8",
            "Cache-Control": "private, no-store",
            "Vary": "Authorization",
        },
    )


def _parse_proppatch(body: bytes) -> list[tuple[str, str, str]]:
    root = _safe_xml_root(body, f"{{{DAV}}}propertyupdate")
    operations: list[tuple[str, str, str]] = []
    for instruction in root:
        if instruction.tag not in {f"{{{DAV}}}set", f"{{{DAV}}}remove"}:
            raise ValueError("PROPPATCH only accepts set and remove instructions")
        prop_nodes = list(instruction)
        if len(prop_nodes) != 1 or prop_nodes[0].tag != f"{{{DAV}}}prop":
            raise ValueError("each PROPPATCH instruction requires exactly one DAV:prop")
        action = "set" if instruction.tag == f"{{{DAV}}}set" else "remove"
        for element in prop_nodes[0]:
            if action == "remove" and (element.attrib or list(element) or (element.text or "").strip()):
                raise ValueError("properties in a remove instruction must be empty")
            clone = DefusedElementTree.fromstring(ElementTree.tostring(element, encoding="utf-8"))
            clone.tail = None
            language = prop_nodes[0].get("{http://www.w3.org/XML/1998/namespace}lang")
            if language and "{http://www.w3.org/XML/1998/namespace}lang" not in clone.attrib:
                clone.set("{http://www.w3.org/XML/1998/namespace}lang", language)
            serialized = ElementTree.tostring(clone, encoding="unicode", short_empty_elements=True)
            if len(serialized.encode("utf-8")) > MAX_PROPERTY_VALUE:
                raise OverflowError("a WebDAV property value is too large")
            operations.append((action, element.tag, serialized))
    if not operations:
        raise ValueError("PROPPATCH contains no property instructions")
    if len(operations) > MAX_PROPERTY_COUNT:
        raise OverflowError("PROPPATCH contains too many property instructions")
    return operations


def _live_property_value_valid(tag: str, serialized: str) -> bool:
    if tag not in MUTABLE_DAV_PROPERTIES | MICROSOFT_CLIENT_PROPERTIES | {MICROSOFT_SPECIAL_FOLDER}:
        return True
    element = DefusedElementTree.fromstring(serialized)
    if list(element):
        return False
    if tag in MICROSOFT_CLIENT_PROPERTIES | {MICROSOFT_SPECIAL_FOLDER} and element.attrib:
        return False
    text = element.text or ""
    if tag == f"{{{DAV}}}displayname":
        return len(text.encode("utf-8")) <= 1024 and "\x00" not in text
    if tag == f"{{{DAV}}}getcontentlanguage":
        return bool(re.fullmatch(r"[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*", text.strip()))
    if len(text.encode("utf-8")) > 256 or "\x00" in text:
        return False
    if tag == MICROSOFT_SPECIAL_FOLDER:
        try:
            value = int(text, 10)
        except ValueError:
            return False
        return -(2**31) <= value < 2**31 and text.strip() == str(value)
    return True


def _proppatch_response(href: str, statuses: list[tuple[str, int]]) -> Response:
    groups: dict[int, list[str]] = {}
    for tag, status in statuses:
        groups.setdefault(status, []).append(_empty_property(tag))
    labels = {200: "OK", 403: "Forbidden", 409: "Conflict", 424: "Failed Dependency", 507: "Insufficient Storage"}
    parts = []
    for status, properties in groups.items():
        error = '<d:error><d:cannot-modify-protected-property/></d:error>' if status == 403 else ""
        parts.append(f'<d:propstat><d:prop>{"".join(properties)}</d:prop><d:status>HTTP/1.1 {status} {labels[status]}</d:status>{error}</d:propstat>')
    xml = f'<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:"><d:response><d:href>{escape(href)}</d:href>{"".join(parts)}</d:response></d:multistatus>'
    return Response(xml, 207, {"Content-Type": "application/xml; charset=utf-8", "Cache-Control": "no-store"})


def _apply_proppatch(username: str, resource: Path, document: dict | None, href: str) -> tuple[Response, bool]:
    body = request.get_data(cache=True)
    try:
        operations = _parse_proppatch(body)
    except OverflowError as exc:
        return Response(str(exc), 413), False
    except PermissionError:
        error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:no-external-entities/></d:error>'
        return Response(error, 400, mimetype="application/xml"), False
    except ValueError as exc:
        return Response(str(exc), 400), False

    protected = {
        index for index, (_, tag, _) in enumerate(operations)
        if tag in PROTECTED_DAV_PROPERTIES or (tag.startswith(f"{{{DAV}}}") and tag not in MUTABLE_DAV_PROPERTIES) or not tag.startswith("{")
    }
    conflicts = {
        index for index, (action, tag, serialized) in enumerate(operations)
        if action == "set" and not _live_property_value_valid(tag, serialized)
    }
    if protected or conflicts:
        statuses = [
            (tag, 403 if index in protected else 409 if index in conflicts else 424)
            for index, (_, tag, _) in enumerate(operations)
        ]
        return _proppatch_response(href, statuses), False

    key = _property_resource_key(username, resource, document)
    path = _properties_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    changed_names: list[str] = []
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _read_json(path, {"version": 1, "resources": {}})
        resources = payload.setdefault("resources", {})
        if not isinstance(resources, dict):
            resources = {}
            payload["resources"] = resources
        current = resources.get(key, {})
        proposed = dict(current) if isinstance(current, dict) else {}
        for action, tag, serialized in operations:
            before = proposed.get(tag)
            if action == "set":
                proposed[tag] = serialized
            else:
                proposed.pop(tag, None)
            if proposed.get(tag) != before:
                changed_names.append(tag)
        if len(proposed) > MAX_STORED_PROPERTIES:
            return _proppatch_response(href, [(tag, 507 if index == 0 else 424) for index, (_, tag, _) in enumerate(operations)]), False
        if changed_names:
            if proposed:
                resources[key] = proposed
            else:
                resources.pop(key, None)
            payload["version"] = 1
            atomic_json_write(path, payload)

    if changed_names:
        audit_key = hashlib.sha256(key.encode("utf-8")).hexdigest()
        _store().history.record(
            "webdav_properties_changed", f"webdav:{username}", "webdav-properties", audit_key,
            {"resource": _store().relative(resource), "properties": sorted(set(changed_names)), "changed_at": utc_now(), "actor": f"webdav:{username}"},
        )
    return _proppatch_response(href, [(tag, 200) for _, tag, _ in operations]), bool(changed_names)


def _copy_dead_properties(
    username: str,
    source: Path,
    source_document: dict | None,
    destination: Path,
    destination_document: dict | None,
) -> None:
    """Preserve dead properties on COPY as recommended by RFC 4918 section 9.8.2."""
    path = _properties_path()
    if not path.exists():
        return
    source_key = _property_resource_key(username, source, source_document)
    destination_key = _property_resource_key(username, destination, destination_document)
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _read_json(path, {"version": 1, "resources": {}})
        resources = payload.get("resources", {})
        source_properties = resources.get(source_key, {}) if isinstance(resources, dict) else {}
        if not isinstance(source_properties, dict) or not source_properties:
            return
        resources[destination_key] = dict(source_properties)
        atomic_json_write(path, payload)
    _store().history.record(
        "webdav_properties_copied", f"webdav:{username}", "webdav-properties",
        hashlib.sha256(destination_key.encode("utf-8")).hexdigest(),
        {
            "source": _store().relative(source),
            "destination": _store().relative(destination),
            "properties": sorted(source_properties),
            "copied_at": utc_now(),
            "actor": f"webdav:{username}",
        },
    )


def _collection_lock_error(resource: Path, username: str) -> Response | None:
    """Require tokens for every explicit lock rooted inside a collection."""
    relative = _store().relative(resource)
    for stored_key, lock in _active_locks().get("locks", {}).items():
        lock_resource = str(lock.get("resource", "")).strip()
        if not lock_resource or not _relative_is_within(lock_resource, relative):
            continue
        if lock.get("username") != username or lock.get("token") != _request_token(stored_key):
            return Response("a member of the collection is locked", 423)
    return None


def _release_collection_locks_after_move(username: str, source: Path) -> None:
    """RFC 4918 section 7.6 forbids moving source locks with a resource."""
    source_relative = _store().relative(source)
    path = _locks_path()
    released: list[dict] = []
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _active_locks()
        locks = payload.get("locks", {})
        for stored_key, lock in list(locks.items()):
            lock_resource = str(lock.get("resource", "")).strip()
            if lock.get("username") != username or not lock_resource or not _relative_is_within(lock_resource, source_relative):
                continue
            released.append(dict(lock))
            locks.pop(stored_key, None)
        if released:
            atomic_json_write(path, payload)
    for lock in released:
        _store().history.record(
            "webdav_lock_released_by_move", f"webdav:{username}", "webdav-locks",
            hashlib.sha256(f"{username}:{lock.get('resource', '')}".encode()).hexdigest(),
            {
                "resource": str(lock.get("resource", "")), "depth": str(lock.get("depth", "0")),
                "released_at": utc_now(), "actor": f"webdav:{username}",
            },
        )

# Export private helpers too so later ordered parts see the same globals
# they had in the original single module.
__all__ = [name for name in globals() if not name.startswith("__")]
