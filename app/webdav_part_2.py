"""WebDAV implementation part 2 of 6."""
from __future__ import annotations

from .webdav_part_1 import *

def _parse_http_etag_list(value: str) -> tuple[bool, list[tuple[bool, str]]]:
    """Parse a bounded RFC 9110 entity-tag list without splitting quoted commas."""
    if not value:
        raise ValueError("HTTP ETag precondition is empty")
    if len(value.encode("latin-1", errors="replace")) > MAX_HTTP_PRECONDITION_BYTES:
        raise OverflowError("HTTP ETag precondition is too large")
    value = value.strip()
    if value == "*":
        return True, []
    tags: list[tuple[bool, str]] = []
    position = 0
    while position < len(value):
        while position < len(value) and value[position] in " \t":
            position += 1
        weak = value[position:position + 2] == "W/"
        if weak:
            position += 2
        if position >= len(value) or value[position] != '"':
            raise ValueError("HTTP ETag precondition contains an invalid entity-tag")
        start = position
        position += 1
        while position < len(value) and value[position] != '"':
            character = ord(value[position])
            if character != 0x21 and not 0x23 <= character <= 0x7E and not 0x80 <= character <= 0xFF:
                raise ValueError("HTTP ETag precondition contains an invalid entity-tag")
            position += 1
        if position >= len(value):
            raise ValueError("HTTP ETag precondition contains an unterminated entity-tag")
        position += 1
        tags.append((weak, value[start:position]))
        if len(tags) > MAX_HTTP_PRECONDITION_TAGS:
            raise OverflowError("HTTP ETag precondition contains too many entity-tags")
        while position < len(value) and value[position] in " \t":
            position += 1
        if position == len(value):
            break
        if value[position] != ",":
            raise ValueError("HTTP ETag precondition is not a valid list")
        position += 1
        if not value[position:].strip():
            raise ValueError("HTTP ETag precondition contains an empty member")
    return False, tags


def _record_http_precondition_failure(
    username: str, resource: Path, condition: str, status: int, reason: str,
) -> None:
    """Audit rejected mutations without retaining client-supplied validators."""
    relative = _store().relative(resource)
    _store().history.record(
        "webdav_http_precondition_rejected",
        f"webdav:{username}",
        "webdav-preconditions",
        hashlib.sha256(f"{username}:{request.method}:{relative}".encode()).hexdigest(),
        {
            "resource": relative,
            "method": request.method,
            "condition": condition,
            "status": status,
            "reason": reason,
            "rejected_at": utc_now(),
            "actor": f"webdav:{username}",
        },
    )


def _http_precondition_error(username: str, resource: Path, document: dict | None) -> Response | None:
    """Evaluate unsafe-request HTTP preconditions in RFC 9110 precedence order."""
    exists = (document is not None and resource.is_file() and not resource.is_symlink()) or (
        resource.is_dir() and not resource.is_symlink()
    )
    current_etag = _etag(document) if document is not None and exists else ""
    modified_at: int | None = None
    headers = {"Cache-Control": "private, no-cache"}
    if exists:
        try:
            modified_at = int(resource.stat().st_mtime)
            headers["Last-Modified"] = formatdate(modified_at, usegmt=True)
        except OSError:
            modified_at = None
    if current_etag:
        headers["ETag"] = current_etag

    def reject(condition: str, reason: str, status: int = 412) -> Response:
        _record_http_precondition_failure(username, resource, condition, status, reason)
        return Response(reason, status, headers)

    if_match = request.headers.get("If-Match")
    if if_match is not None:
        try:
            wildcard, tags = _parse_http_etag_list(if_match)
        except OverflowError as exc:
            return reject("If-Match", str(exc), 413)
        except ValueError as exc:
            return reject("If-Match", str(exc), 400)
        matches = exists and (
            wildcard or bool(current_etag) and any(
                not weak and hmac.compare_digest(tag, current_etag) for weak, tag in tags
            )
        )
        if not matches:
            return reject("If-Match", "If-Match precondition failed")
    else:
        unmodified = request.headers.get("If-Unmodified-Since")
        unmodified_at = _http_date_timestamp(unmodified) if unmodified else None
        if unmodified_at is not None and modified_at is not None and modified_at > unmodified_at:
            return reject("If-Unmodified-Since", "If-Unmodified-Since precondition failed")

    if_none_match = request.headers.get("If-None-Match")
    if if_none_match is not None:
        try:
            wildcard, tags = _parse_http_etag_list(if_none_match)
        except OverflowError as exc:
            return reject("If-None-Match", str(exc), 413)
        except ValueError as exc:
            return reject("If-None-Match", str(exc), 400)
        matches = exists and (
            wildcard or bool(current_etag) and any(
                hmac.compare_digest(tag, current_etag) for _weak, tag in tags
            )
        )
        if matches:
            return reject("If-None-Match", "If-None-Match precondition failed")
    return None


def _digest_value(algorithm: str, digest: bytes) -> str:
    """Serialize an RFC 9530 digest as an RFC 8941 Byte Sequence."""
    return f"{algorithm}=:{base64.b64encode(digest).decode('ascii')}:"


def _parse_digest_field(value: str) -> dict[str, bytes]:
    """Parse the supported subset of the RFC 9530 Structured Field dictionary.

    Digest algorithms use byte-sequence values. Parameters, duplicate keys and
    malformed Base64 are rejected instead of being interpreted ambiguously.
    Unsupported algorithms are ignored only when at least one supported active
    algorithm can be verified.
    """
    if not value or len(value.encode("utf-8")) > MAX_DIGEST_FIELD_BYTES:
        raise ValueError("Content-Digest is empty or too large")
    parsed: dict[str, bytes] = {}
    saw_member = False
    for raw_member in value.split(","):
        member = raw_member.strip()
        if not member:
            raise ValueError("Content-Digest contains an empty member")
        key, separator, encoded = member.partition("=")
        key = key.strip().casefold()
        saw_member = True
        if separator != "=" or not re.fullmatch(r"[a-z*][a-z0-9_.*-]*", key):
            raise ValueError("Content-Digest is not a valid dictionary")
        if key in parsed:
            raise ValueError("Content-Digest contains a duplicate algorithm")
        if key not in DIGEST_ALGORITHMS:
            continue
        encoded = encoded.strip()
        if ";" in encoded or len(encoded) < 2 or encoded[0] != ":" or encoded[-1] != ":":
            raise ValueError("Content-Digest requires a byte-sequence value")
        try:
            decoded = base64.b64decode(encoded[1:-1], validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("Content-Digest contains invalid Base64") from None
        expected_length = DIGEST_ALGORITHMS[key][1]
        if len(decoded) != expected_length:
            raise ValueError(f"Content-Digest {key} has the wrong length")
        parsed[key] = decoded
    if not saw_member or not parsed:
        raise ValueError("Content-Digest has no supported active algorithm")
    return parsed


def _digest_audit(username: str, resource: Path, action: str, algorithms: list[str], size: int) -> None:
    """Record integrity decisions without copying client-supplied digest values."""
    relative = _store().relative(resource)
    _store().history.record(
        action,
        f"webdav:{username}",
        "webdav-integrity",
        hashlib.sha256(f"{username}:{relative}".encode()).hexdigest(),
        {
            "resource": relative,
            "algorithms": sorted(algorithms),
            "size": size,
            "checked_at": utc_now(),
            "actor": f"webdav:{username}",
        },
    )


def _verify_content_digest(content: bytes, username: str, resource: Path) -> Response | None:
    """Fail a PUT before quota checks or storage mutation when its digest differs."""
    supplied = request.headers.get("Content-Digest")
    if supplied is None:
        return None
    try:
        parsed = _parse_digest_field(supplied)
    except ValueError as exc:
        _digest_audit(username, resource, "webdav_content_digest_rejected", [], len(content))
        return Response(str(exc), 400, {"Want-Content-Digest": DIGEST_PREFERENCE})
    algorithms = list(parsed)
    mismatched = any(
        not hmac.compare_digest(factory(content).digest(), parsed[name])
        for name, (factory, _length, _weight) in DIGEST_ALGORITHMS.items()
        if name in parsed
    )
    if mismatched:
        _digest_audit(username, resource, "webdav_content_digest_mismatch", algorithms, len(content))
        return Response("Content-Digest does not match the uploaded content", 422, {"Want-Content-Digest": DIGEST_PREFERENCE})
    _digest_audit(username, resource, "webdav_content_digest_verified", algorithms, len(content))
    return None


def _content_digest_for_range(handle, start: int, end: int) -> bytes:
    digest = hashlib.sha256()
    for chunk in _iter_file_range(handle, start, end):
        digest.update(chunk)
    return digest.digest()


def _parse_byte_ranges(value: str, size: int) -> list[tuple[int, int]]:
    """Parse a bounded RFC 9110 bytes range-set or raise ValueError for 416."""
    unit, separator, ranges_value = value.partition("=")
    if separator != "=" or unit.strip().casefold() != "bytes":
        raise ValueError("unsupported range unit")
    specifications = [item.strip() for item in ranges_value.split(",")]
    if not specifications or any(not item for item in specifications) or len(specifications) > MAX_BYTE_RANGES:
        raise ValueError("invalid or excessive range set")
    ranges: list[tuple[int, int]] = []
    for specification in specifications:
        first, dash, last = specification.partition("-")
        if dash != "-" or (not first and not last):
            raise ValueError("invalid byte range")
        try:
            if not first:
                suffix = int(last)
                if suffix <= 0 or size <= 0:
                    continue
                start, end = max(0, size - suffix), size - 1
            else:
                start = int(first)
                if start < 0:
                    raise ValueError
                if start >= size:
                    continue
                end = size - 1 if not last else min(int(last), size - 1)
                if end < start:
                    raise ValueError
        except (TypeError, ValueError):
            raise ValueError("invalid byte range") from None
        ranges.append((start, end))
    if not ranges:
        raise ValueError("unsatisfiable byte range")
    ordered = sorted(ranges)
    if any(current[0] <= previous[1] for previous, current in zip(ordered, ordered[1:])):
        raise ValueError("overlapping ranges are rejected")
    return ranges


def _iter_file_range(handle, start: int, end: int):
    remaining = end - start + 1
    handle.seek(start)
    while remaining:
        chunk = handle.read(min(DOWNLOAD_CHUNK_SIZE, remaining))
        if not chunk:
            break
        remaining -= len(chunk)
        yield chunk


def _download_response(path: Path, username: str, document: dict, media_type: str) -> Response:
    """Return a conditional, range-capable response from one stable open-file snapshot."""
    try:
        handle = path.open("rb")
    except OSError:
        return Response("not found", 404)
    try:
        stat = os.fstat(handle.fileno())
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(DOWNLOAD_CHUNK_SIZE), b""):
            digest.update(chunk)
        handle.seek(0)
        size = stat.st_size
        etag = f'"{digest.hexdigest()}"'
        representation_digest = _digest_value("sha-256", digest.digest())
        last_modified = formatdate(stat.st_mtime, usegmt=True)
        headers = {
            "ETag": etag,
            "Repr-Digest": representation_digest,
            "Last-Modified": last_modified,
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, no-cache",
        }
        content_language = _content_language(username, path, document)
        if content_language:
            headers["Content-Language"] = content_language

        if_match = request.headers.get("If-Match")
        if if_match is not None and not _etag_list_matches(if_match, etag, weak=False):
            handle.close()
            return Response("", 412, headers)
        if if_match is None:
            unmodified = request.headers.get("If-Unmodified-Since")
            unmodified_at = _http_date_timestamp(unmodified) if unmodified else None
            if unmodified_at is not None and int(stat.st_mtime) > unmodified_at:
                handle.close()
                return Response("", 412, headers)

        if_none_match = request.headers.get("If-None-Match")
        if if_none_match is not None and _etag_list_matches(if_none_match, etag, weak=True):
            handle.close()
            return Response("", 304, headers)
        if if_none_match is None:
            modified = request.headers.get("If-Modified-Since")
            modified_at = _http_date_timestamp(modified) if modified else None
            if modified_at is not None and int(stat.st_mtime) <= modified_at:
                handle.close()
                return Response("", 304, headers)

        range_header = request.headers.get("Range") if request.method == "GET" else None
        if range_header and request.headers.get("If-Range"):
            validator = request.headers["If-Range"].strip()
            if validator.startswith('"') or validator.startswith("W/"):
                range_allowed = not validator.startswith("W/") and validator == etag
            else:
                range_allowed = validator == last_modified
            if not range_allowed:
                range_header = None

        if range_header:
            try:
                ranges = _parse_byte_ranges(range_header, size)
            except ValueError:
                handle.close()
                return Response("", 416, {**headers, "Content-Range": f"bytes */{size}"})
            if len(ranges) == 1:
                start, end = ranges[0]
                response_headers = {
                    **headers,
                    "Content-Digest": _digest_value("sha-256", _content_digest_for_range(handle, start, end)),
                    "Content-Type": media_type,
                    "Content-Length": str(end - start + 1),
                    "Content-Range": f"bytes {start}-{end}/{size}",
                }

                def single_range():
                    try:
                        yield from _iter_file_range(handle, start, end)
                    finally:
                        handle.close()

                return Response(single_range(), 206, response_headers)

            boundary = f"simpleoffice-{digest.hexdigest()[:24]}"
            parts: list[tuple[bytes, int, int]] = []
            total_length = 0
            for start, end in ranges:
                prefix = (
                    f"--{boundary}\r\nContent-Type: {media_type}\r\n"
                    f"Content-Range: bytes {start}-{end}/{size}\r\n\r\n"
                ).encode("ascii")
                parts.append((prefix, start, end))
                total_length += len(prefix) + end - start + 1 + 2
            closing = f"--{boundary}--\r\n".encode("ascii")
            total_length += len(closing)
            content_digest = hashlib.sha256()
            for prefix, start, end in parts:
                content_digest.update(prefix)
                for chunk in _iter_file_range(handle, start, end):
                    content_digest.update(chunk)
                content_digest.update(b"\r\n")
            content_digest.update(closing)

            def multiple_ranges():
                try:
                    for prefix, start, end in parts:
                        yield prefix
                        yield from _iter_file_range(handle, start, end)
                        yield b"\r\n"
                    yield closing
                finally:
                    handle.close()

            return Response(
                multiple_ranges(), 206,
                {
                    **headers,
                    "Content-Digest": _digest_value("sha-256", content_digest.digest()),
                    "Content-Type": f"multipart/byteranges; boundary={boundary}",
                    "Content-Length": str(total_length),
                    "X-Content-Type-Options": "nosniff",
                    "Content-Security-Policy": "sandbox",
                },
            )

        if request.method == "HEAD":
            handle.close()
            response = Response(None, 200)
            response.headers.update({**headers, "Content-Type": media_type, "Content-Length": str(size)})
            return response

        def complete_file():
            try:
                yield from _iter_file_range(handle, 0, size - 1)
            finally:
                handle.close()

        return Response(
            complete_file(), 200,
            {
                **headers,
                "Content-Digest": representation_digest,
                "Content-Type": media_type,
                "Content-Length": str(size),
            },
        )
    except Exception:
        handle.close()
        raise


def _resource_url(username: str, document: dict, *, external: bool = False) -> str:
    filename = Path(str(document.get("last_path", "document"))).name
    return url_for("webdav.endpoint", path=f"documents/{username}/{document['document_id']}--{filename}", _external=external)


def _tree_url(username: str, relative: str = "", *, external: bool = False, collection: bool = False) -> str:
    encoded = "/".join(quote(part, safe="") for part in Path(relative).parts if part not in {"", "."})
    suffix = f"/{encoded}" if encoded else ""
    base = request.url_root.rstrip("/") if external else ""
    value = f"{base}/webdav/files/{quote(username, safe='')}{suffix}"
    return value + "/" if collection and not value.endswith("/") else value


def _tree_path(relative_path: str) -> Path:
    store = _store()
    if not relative_path.strip("/"):
        return store.root
    relative = store._safe_managed_relative_path(unquote(relative_path), require_name=True)
    candidate = store.root / relative
    if candidate.is_symlink():
        raise ValueError("symbolic links are not available over WebDAV")
    return candidate


def _portable_name_key(name: str) -> str:
    """Return a stable, conservative comparison key for desktop file systems."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", name).casefold())


def _portable_name_reason(name: str) -> str:
    """Explain why a new WebDAV member would not round-trip across target clients."""
    if not name or name in {".", ".."}:
        return "empty-or-relative-segment"
    if name != unicodedata.normalize("NFC", name):
        return "unicode-nfc-required"
    if name.startswith(" ") or name.endswith((" ", ".")):
        return "leading-or-trailing-space-or-dot"
    if any(character in WINDOWS_FORBIDDEN_NAME_CHARACTERS for character in name):
        return "windows-reserved-character"
    if any(character in BIDI_CONTROL_CHARACTERS for character in name):
        return "bidirectional-control-character"
    if any(unicodedata.category(character) in {"Cc", "Cs", "Co", "Cn"} for character in name):
        return "non-interchange-character"
    basename = name.rstrip(" .").split(".", 1)[0].upper()
    if basename in WINDOWS_RESERVED_BASENAMES:
        return "windows-reserved-device-name"
    try:
        encoded_length = len(name.encode("utf-8"))
    except UnicodeEncodeError:
        return "invalid-unicode"
    if encoded_length > MAX_PORTABLE_NAME_BYTES:
        return "name-too-long"
    return ""


def _portable_name_error(
    username: str,
    resource: Path,
    *,
    exclude: Path | None = None,
) -> Response | None:
    """Reject ambiguous new names while leaving existing legacy resources operable."""
    siblings: list[Path] = []
    if resource.parent.is_dir() and not resource.parent.is_symlink():
        siblings = list(resource.parent.iterdir())
        if any(sibling != exclude and sibling.name == resource.name for sibling in siblings):
            return None
    elif resource.exists():
        return None
    reason = _portable_name_reason(resource.name)
    if not reason and siblings:
        requested_key = _portable_name_key(resource.name)
        for sibling in siblings:
            if sibling == exclude or sibling.name in {CONTROL_DIR, HISTORY_DIR, POLICY_FILE}:
                continue
            if _portable_name_key(sibling.name) == requested_key:
                reason = "case-or-normalization-collision"
                break
    if not reason:
        return None

    parent = _store().relative(resource.parent)
    name_digest = hashlib.sha256(resource.name.encode("utf-8", errors="surrogatepass")).hexdigest()
    actor = f"webdav:{username}"
    _store().history.record(
        "webdav_portable_name_rejected",
        actor,
        "webdav-name-policy",
        hashlib.sha256(f"{username}:{parent}:{name_digest}".encode()).hexdigest(),
        {
            "actor": actor,
            "method": request.method,
            "parent": parent,
            "name_sha256": name_digest,
            "name_utf8_bytes": len(resource.name.encode("utf-8", errors="surrogatepass")),
            "reason": reason,
            "rejected_at": utc_now(),
        },
    )
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<d:error xmlns:d="DAV:" xmlns:s="urn:simpleoffice:webdav">'
        f'<s:portable-file-name reason="{reason}"/>'
        '</d:error>'
    )
    return Response(
        xml,
        409,
        {
            "Content-Type": "application/xml; charset=utf-8",
            "Cache-Control": "no-store",
            "X-SimpleOffice-Name-Reason": reason,
        },
    )


def _tree_document(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        raise ValueError("document unavailable")
    return _store().get_document(path)


def _lock_key(path: Path, document: dict | None = None) -> str:
    if document:
        return str(document["document_id"])
    relative = _store().relative(path)
    return "unmapped:" + hashlib.sha256(relative.encode("utf-8")).hexdigest()


def _destination(username: str, identity: dict) -> tuple[Path, str]:
    value = request.headers.get("Destination", "")
    if not value:
        raise ValueError("Destination header is required")
    parsed = urlsplit(value)
    if parsed.netloc and parsed.netloc.casefold() != request.host.casefold():
        raise PermissionError("cross-server destinations are not allowed")
    prefix = f"/webdav/files/{username}/"
    path = unquote(parsed.path)
    if not path.startswith(prefix):
        raise PermissionError("destination must remain in the authenticated user's WebDAV tree")
    relative = path[len(prefix):].strip("/")
    if not relative:
        raise ValueError("the WebDAV root cannot be replaced")
    destination = _tree_path(relative)
    if not _credential_allows_path(identity, destination):
        raise PermissionError("destination is outside the credential's collection")
    return destination, _store().relative(destination)


def _lock_for(key: str) -> dict | None:
    return _active_locks().get("locks", {}).get(key)


def _relative_is_within(candidate: str, collection: str) -> bool:
    candidate_path = Path(candidate or ".")
    collection_path = Path(collection or ".")
    return candidate_path == collection_path or collection_path in candidate_path.parents


def _lock_applies(stored_key: str, lock: dict, resource: Path, document: dict | None) -> bool:
    key = _lock_key(resource, document)
    if stored_key == key:
        return True
    root = str(lock.get("resource", "")).strip()
    if not root or str(lock.get("depth", "0")) != "infinity":
        return False
    relative = _store().relative(resource)
    return relative != root and _relative_is_within(relative, root)


def _locks_for(resource: Path, document: dict | None = None, locks: dict | None = None) -> list[tuple[str, dict]]:
    active = locks if locks is not None else _active_locks().get("locks", {})
    return [
        (stored_key, lock) for stored_key, lock in active.items()
        if _lock_applies(stored_key, lock, resource, document)
    ]


def _conflicting_locks(resource: Path, document: dict | None, depth: str) -> list[tuple[str, dict]]:
    active = _active_locks().get("locks", {})
    conflicts = _locks_for(resource, document, active)
    if depth == "infinity" and resource.is_dir() and not resource.is_symlink():
        relative = _store().relative(resource)
        known = {stored_key for stored_key, _lock in conflicts}
        for stored_key, lock in active.items():
            lock_resource = str(lock.get("resource", "")).strip()
            if stored_key not in known and lock_resource and lock_resource != relative and _relative_is_within(lock_resource, relative):
                conflicts.append((stored_key, lock))
    return conflicts


def _require_lock(resource: Path, document: dict | None, username: str) -> Response | None:
    locks = _locks_for(resource, document)
    token = _request_token(_lock_key(resource, document))
    for _stored_key, lock in locks:
        if lock.get("token") != token or lock.get("username") != username:
            return Response("resource is locked", 423)
    return None


def _release_lock(key: str) -> None:
    path = _locks_path()
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _active_locks()
        payload.get("locks", {}).pop(key, None)
        atomic_json_write(path, payload)


def _active_locks() -> dict:
    if request.method == "PROPFIND" and hasattr(g, "_webdav_propfind_locks"):
        return g._webdav_propfind_locks
    now = datetime.now(timezone.utc)
    payload = _read_json(_locks_path(), {"locks": {}})
    locks = payload.setdefault("locks", {})
    locks = {key: value for key, value in locks.items() if datetime.fromisoformat(value["expires_at"]).astimezone(timezone.utc) > now}
    payload["locks"] = locks
    if request.method == "PROPFIND":
        g._webdav_propfind_locks = payload
    return payload


def _parse_if_header(value: str) -> list[tuple[str | None, list[list[tuple[bool, str, str]]]]]:
    """Parse the bounded RFC 4918 If grammar without accepting loose substrings."""
    if len(value.encode("utf-8")) > MAX_IF_HEADER_BYTES:
        raise OverflowError("WebDAV If header is too large")
    position = 0
    list_count = 0
    condition_count = 0

    def whitespace() -> None:
        nonlocal position
        while position < len(value) and value[position] in " \t":
            position += 1

    def enclosed(start: str, end: str) -> str:
        nonlocal position
        if position >= len(value) or value[position] != start:
            raise ValueError("invalid WebDAV If header")
        closing = value.find(end, position + 1)
        if closing < 0:
            raise ValueError("invalid WebDAV If header")
        result = value[position + 1:closing]
        if not result or "\r" in result or "\n" in result or start in result:
            raise ValueError("invalid WebDAV If header")
        position = closing + 1
        return result

    def condition_list() -> list[tuple[bool, str, str]]:
        nonlocal position, list_count, condition_count
        if list_count >= MAX_IF_LISTS:
            raise OverflowError("WebDAV If header contains too many lists")
        list_count += 1
        position += 1
        conditions: list[tuple[bool, str, str]] = []
        while True:
            whitespace()
            if position >= len(value):
                raise ValueError("invalid WebDAV If header")
            if value[position] == ")":
                position += 1
                if not conditions:
                    raise ValueError("WebDAV If lists must not be empty")
                return conditions
            negated = False
            if value[position:position + 3].casefold() == "not":
                following = position + 3
                if following >= len(value) or value[following] not in " \t":
                    raise ValueError("invalid Not condition in WebDAV If header")
                negated = True
                position = following
                whitespace()
            if condition_count >= MAX_IF_CONDITIONS:
                raise OverflowError("WebDAV If header contains too many conditions")
            condition_count += 1
            if value[position] == "<":
                conditions.append((negated, "token", enclosed("<", ">")))
            elif value[position] == "[":
                conditions.append((negated, "etag", enclosed("[", "]")))
            else:
                raise ValueError("invalid condition in WebDAV If header")

    whitespace()
    if not value or position == len(value):
        return []
    groups: list[tuple[str | None, list[list[tuple[bool, str, str]]]]] = []
    if value[position] == "(":
        lists = []
        while True:
            whitespace()
            if position >= len(value):
                break
            if value[position] != "(":
                raise ValueError("tagged and untagged WebDAV If lists cannot be mixed")
            lists.append(condition_list())
        groups.append((None, lists))
        return groups
    while position < len(value):
        whitespace()
        if position >= len(value):
            break
        tag = enclosed("<", ">")
        whitespace()
        lists = []
        while position < len(value) and value[position] == "(":
            lists.append(condition_list())
            whitespace()
        if not lists:
            raise ValueError("tagged WebDAV If resource requires a condition list")
        groups.append((tag, lists))
    return groups


def _if_resource(tag: str | None, username: str, identity: dict) -> dict:
    """Resolve a tagged URI only inside the authenticated WebDAV namespace."""
    parsed = urlsplit(tag or request.path)
    if parsed.netloc and parsed.netloc.casefold() != request.host.casefold():
        raise PermissionError("WebDAV If resource belongs to another server")
    if parsed.query or parsed.fragment:
        raise ValueError("WebDAV If resource must not contain a query or fragment")
    path = unquote(parsed.path)
    tree_prefix = f"/webdav/files/{username}"
    stable_prefix = f"/webdav/documents/{username}/"
    if path.rstrip("/") == tree_prefix:
        relative = ""
        resource = _tree_path(relative)
    elif path.startswith(tree_prefix + "/"):
        relative = path[len(tree_prefix):].strip("/")
        resource = _tree_path(relative)
    elif path.startswith(stable_prefix):
        leaf = path[len(stable_prefix):]
        if "/" in leaf or "--" not in leaf:
            raise ValueError("invalid stable WebDAV resource")
        document_id, requested_name = leaf.split("--", 1)
        document = _store().get_document(document_id)
        resource = _document_path(document)
        if requested_name != resource.name:
            raise ValueError("invalid stable WebDAV resource")
    else:
        raise PermissionError("WebDAV If resource is outside this user tree")
    if not _credential_allows_path(identity, resource):
        raise PermissionError("WebDAV If resource is outside this credential")
    if not _vfs().allows(username, resource, "read"):
        raise PermissionError("WebDAV If resource is outside this user's folder rights")
    document = None
    if resource.is_file() and not resource.is_symlink():
        document = _tree_document(resource)
    collection = resource.is_dir() and not resource.is_symlink()
    return {
        "resource": resource,
        "document": document,
        "collection": collection,
        "key": _lock_key(resource, document),
        "etag": _etag(document) if document else "",
    }

# Export private helpers too so later ordered parts see the same globals
# they had in the original single module.
__all__ = [name for name in globals() if not name.startswith("__")]
