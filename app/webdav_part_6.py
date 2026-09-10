"""WebDAV implementation part 6 of 6."""
from __future__ import annotations

from .webdav_part_5 import *

@bp.route("/webdav/files/<username>", defaults={"relative_path": ""}, methods=["OPTIONS", "PROPFIND", "PROPPATCH", "REPORT", "SEARCH", "GET", "HEAD", "PUT", "DELETE", "MKCOL", "COPY", "MOVE", "LOCK", "UNLOCK"])
@bp.route("/webdav/files/<username>/<path:relative_path>", methods=["OPTIONS", "PROPFIND", "PROPPATCH", "REPORT", "SEARCH", "GET", "HEAD", "PUT", "DELETE", "MKCOL", "COPY", "MOVE", "LOCK", "UNLOCK"])
def file_tree(username: str, relative_path: str):
    """Hierarchical WebDAV namespace for desktop file managers and sync clients."""
    identity = _authenticate()
    if identity is None:
        return _unauthorized()
    if identity["username"] != username:
        return Response("not found", 404)
    g._webdav_identity = identity
    allow = "OPTIONS, PROPFIND, REPORT, SEARCH, GET, HEAD" if identity["scope"] == "read" else "OPTIONS, PROPFIND, PROPPATCH, REPORT, SEARCH, GET, HEAD, PUT, DELETE, MKCOL, COPY, MOVE, LOCK, UNLOCK"
    try:
        resource = _tree_path(relative_path)
    except ValueError:
        return Response("not found", 404)
    if not _credential_allows_path(identity, resource):
        return Response("not found", 404)
    if request.method == "OPTIONS":
        return Response("", 204, {
            "DAV": "1, 2, sync-collection", "MS-Author-Via": "DAV", "Allow": allow,
            "DASL": "<DAV:basicsearch>", "Want-Content-Digest": DIGEST_PREFERENCE,
            "Cache-Control": "private, no-store", "Vary": "Authorization",
        })
    acl_target = resource if resource.exists() else resource.parent
    if not _vfs().allows(username, acl_target, "read"):
        # Hidden resources are indistinguishable from absent resources.
        return Response("not found", 404)
    if identity["scope"] != "write" and request.method in WRITE_METHODS:
        return _need_privileges_response(
            request.path, _missing_method_privilege(request.method), allow,
        )
    if request.method in WRITE_METHODS:
        write_target = resource.parent if request.method in {"MKCOL"} or (request.method in {"PUT", "LOCK"} and not resource.exists()) else resource
        if not _vfs().allows(username, write_target, "write"):
            return _need_privileges_response(
                request.path, _missing_method_privilege(request.method), allow,
            )
        if request.method in {"DELETE", "MOVE"} and resource != _store().root and not _vfs().allows(username, resource.parent, "write"):
            return _need_privileges_response(
                request.path, _missing_method_privilege(request.method), allow,
            )
    is_collection = resource.is_dir() and not resource.is_symlink()
    document = None
    if resource.is_file() and not resource.is_symlink():
        try:
            document = _tree_document(resource)
        except ValueError:
            return Response("not found", 404)

    if request.method in WRITE_METHODS:
        mutation_lock = exclusive_file_lock(_sync_path().with_suffix(".mutation.lock"))
        mutation_lock.__enter__()
        g._webdav_mutation_lock = mutation_lock
        sync_error = _sync_if_error(username, identity)
        if sync_error is not None:
            return sync_error

    if request.method == "REPORT":
        if not is_collection:
            return Response("sync-collection requires a collection", 400)
        mutation_lock = exclusive_file_lock(_sync_path().with_suffix(".mutation.lock"))
        mutation_lock.__enter__()
        g._webdav_mutation_lock = mutation_lock
        return _sync_report(username, resource)

    if request.method == "SEARCH":
        if not is_collection and document is None:
            return Response("not found", 404)
        return _search_response(username, identity, resource)

    if request.method == "PROPFIND":
        if not is_collection and document is None:
            return Response("not found", 404)
        depth = request.headers.get("Depth", "infinity").casefold()
        if depth not in {"0", "1", "infinity"}:
            return Response("PROPFIND Depth must be 0, 1 or infinity", 400)
        body = request.get_data(cache=True)
        try:
            query = _parse_propfind(body)
        except OverflowError as exc:
            return Response(str(exc), 413)
        except PermissionError:
            error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:no-external-entities/></d:error>'
            return Response(error, 400, mimetype="application/xml")
        except ValueError as exc:
            return Response(str(exc), 400)

        # Keep a complete tree response consistent with the same lock used by
        # PUT, COPY, MOVE, DELETE and property mutations. The XML is fully
        # assembled before sending, so a client never receives a partial
        # success followed by a server-side resource-limit failure.
        mutation_lock = exclusive_file_lock(_sync_path().with_suffix(".mutation.lock"))
        mutation_lock.__enter__()
        g._webdav_mutation_lock = mutation_lock
        is_collection = resource.is_dir() and not resource.is_symlink()
        document = None
        if resource.is_file() and not resource.is_symlink():
            try:
                document = _tree_document(resource)
            except ValueError:
                return Response("not found", 404)
        elif not is_collection:
            return Response("not found", 404)
        effective_depth = depth if is_collection else "0"
        href = _tree_url(username, _store().relative(resource), collection=is_collection)
        wants_sync_token = query[0] == "propname" or f"{{{DAV}}}sync-token" in query[1]
        sync_token = _collection_sync_token(username, resource) if is_collection and wants_sync_token else ""
        responses: list[str] = []
        response_size = len((PROPFIND_XML_PREFIX + PROPFIND_XML_SUFFIX).encode("utf-8"))
        try:
            response_size = _append_propfind_response(
                responses,
                _prop_response(href, resource.name if resource != _store().root else "SimpleOffice Dokumente", collection=is_collection, document=document, sync_token=sync_token, username=username, resource=resource, query=query, searchable=True),
                response_size,
            )
            for child, child_is_collection, child_document in _propfind_members(resource, effective_depth, username):
                child_href = _tree_url(
                    username, _store().relative(child), collection=child_is_collection,
                )
                response_size = _append_propfind_response(
                    responses,
                    _prop_response(
                        child_href,
                        child.name,
                        collection=child_is_collection,
                        document=child_document,
                        username=username,
                        resource=child,
                        query=query,
                        searchable=True,
                    ),
                    response_size,
                )
            xml = _propfind_multistatus(responses)
        except _PropfindLimitError as exc:
            return _propfind_limit_response(username, resource, exc)
        return Response(
            xml,
            207,
            {
                "Content-Type": "application/xml; charset=utf-8",
                "Cache-Control": "private, no-store",
                "Vary": "Authorization, Depth",
            },
        )

    if request.method in {"GET", "HEAD"}:
        if document is None:
            return Response("not found", 404)
        return _download_response(
            resource, username, document,
            mimetypes.guess_type(resource.name)[0] or "application/octet-stream",
        )

    key = _lock_key(resource, document)
    lock_error = _require_lock(resource, document, username)
    if lock_error is not None and request.method not in {"LOCK", "UNLOCK", "COPY"}:
        return lock_error

    if request.method == "PROPPATCH":
        if not is_collection and document is None:
            return Response("not found", 404)
        precondition_error = _http_precondition_error(username, resource, document)
        if precondition_error is not None:
            return precondition_error
        if document is not None:
            try:
                _store()._require_document_editable(document)
            except ValueError as exc:
                return Response(str(exc), 423)
        href = _tree_url(username, _store().relative(resource), collection=is_collection)
        response, changed = _apply_proppatch(username, resource, document, href)
        if changed:
            _record_sync_changes(username, _store().relative(resource))
        return response

    if request.method == "MKCOL":
        if resource.exists():
            return Response("resource already exists", 405)
        if request.get_data():
            return Response("extended MKCOL bodies are not supported", 415)
        precondition_error = _http_precondition_error(username, resource, None)
        if precondition_error is not None:
            return precondition_error
        name_error = _portable_name_error(username, resource)
        if name_error is not None:
            return name_error
        try:
            _store().create_collection(_store().relative(resource), f"webdav:{username}")
        except ValueError:
            return Response("parent collection does not exist", 409)
        _record_sync_changes(username, _store().relative(resource))
        return Response("", 201)

    if request.method == "LOCK":
        name_error = _portable_name_error(username, resource)
        if name_error is not None:
            return name_error
        return _lock_request(username, resource, document, request.url)

    if request.method == "UNLOCK":
        token, token_error = _unlock_token()
        if token_error is not None:
            return token_error
        existing = _lock_for(key)
        if not existing or existing.get("token") != token or existing.get("username") != username:
            return Response("lock token does not match", 409)
        _release_lock(key)
        _record_lock_audit("webdav_lock_released", username, resource, existing)
        return Response("", 204)

    if request.method == "PUT":
        current_etag = _etag(document) if document else ""
        if document:
            precondition_error = _http_precondition_error(username, resource, document)
            if precondition_error is not None:
                return precondition_error
            if_match = request.headers.get("If-Match", "")
            if not _request_token(key) and not if_match:
                return Response("existing resources require If-Match or a lock token", 428, {"ETag": current_etag})
            content = request.get_data()
            digest_error = _verify_content_digest(content, username, resource)
            if digest_error is not None:
                return digest_error
            quota_error = _check_quota(username, "PUT", resource, len(content) - resource.stat().st_size)
            if quota_error is not None:
                return quota_error
            scan_error = _webdav_upload_scan_error(content, username, resource)
            if scan_error is not None:
                return scan_error
            try:
                updated = _store().replace_content(document["document_id"], content, f"webdav:{username}", expected_sha256=_etag_value(current_etag), max_bytes=int(current_app.config["MAX_CONTENT_LENGTH"]))
            except ValueError as exc:
                status = 412 if "changed since" in str(exc) else 423 if "locked" in str(exc) or "staged" in str(exc) else 400
                return Response(str(exc), status)
            if _etag(updated) != current_etag:
                _record_sync_changes(username, _store().relative(resource))
            return Response("", 204, _stored_integrity_headers(updated))
        if is_collection:
            return Response("cannot PUT a collection", 405)
        precondition_error = _http_precondition_error(username, resource, None)
        if precondition_error is not None:
            return precondition_error
        name_error = _portable_name_error(username, resource)
        if name_error is not None:
            return name_error
        content = request.get_data()
        digest_error = _verify_content_digest(content, username, resource)
        if digest_error is not None:
            return digest_error
        quota_error = _check_quota(username, "PUT", resource, len(content))
        if quota_error is not None:
            return quota_error
        scan_error = _webdav_upload_scan_error(content, username, resource)
        if scan_error is not None:
            return scan_error
        try:
            created = _store().create_document_at(_store().relative(resource), content, f"webdav:{username}", max_bytes=int(current_app.config["MAX_CONTENT_LENGTH"]))
        except FileExistsError:
            return Response("resource already exists", 412)
        except ValueError as exc:
            return Response(str(exc), 409)
        old_lock = _lock_for(key)
        if old_lock:
            _release_lock(key)
            _save_lock(
                created["document_id"], username, old_lock["token"], _timeout_seconds(), old_lock.get("owner", ""),
                href=str(old_lock.get("href", request.url)), depth=str(old_lock.get("depth", "0")),
                resource=_store().relative(resource),
            )
        _record_sync_changes(username, _store().relative(resource))
        return Response("", 201, _stored_integrity_headers(created))

    if request.method == "DELETE":
        if document:
            precondition_error = _http_precondition_error(username, resource, document)
            if precondition_error is not None:
                return precondition_error
            try:
                _store().soft_delete_document(document["document_id"], f"webdav:{username}")
            except ValueError as exc:
                return Response(str(exc), 423)
            _release_lock(key)
            _record_sync_changes(username, _store().relative(resource))
            return Response("", 204)
        if is_collection:
            if resource == _store().root:
                return Response("the WebDAV root cannot be deleted", 403)
            if _credential_is_boundary(identity, resource):
                return Response("the credential lacks access to the parent collection", 403)
            depth = request.headers.get("Depth", "infinity").lower()
            if depth != "infinity":
                return Response("collection DELETE requires Depth: infinity", 400)
            precondition_error = _http_precondition_error(username, resource, None)
            if precondition_error is not None:
                return precondition_error
            collection_lock_error = _collection_lock_error(resource, username)
            if collection_lock_error is not None:
                return collection_lock_error
            try:
                result = _store().soft_delete_collection(
                    _store().relative(resource), f"webdav:{username}",
                )
            except OSError:
                return _quota_error(username, "DELETE", resource, 0, "sufficient-disk-space")
            except (RuntimeError, ValueError) as exc:
                status = 507 if "too many" in str(exc) or "nesting depth" in str(exc) else 423 if "locked" in str(exc) or "staged" in str(exc) else 409
                return Response(str(exc), status)
            changed_paths = []
            source_relative = str(result["path"])
            for nested in result["directories_relative"]:
                changed_paths.append(source_relative if nested == Path(".") else str(Path(source_relative) / nested))
            changed_paths.extend(str(item.get("deleted_from", "")) for item in result["resources"])
            _release_collection_locks_after_delete(username, resource)
            _record_sync_changes(username, *changed_paths)
            return Response("", 204)
        return Response("not found", 404)

    if request.method in {"COPY", "MOVE"}:
        if document is None and not is_collection:
            return Response("not found", 404)
        if resource == _store().root:
            return Response("the WebDAV root cannot be copied or moved", 403)
        if request.method == "MOVE" and _credential_is_boundary(identity, resource):
            return Response("the credential lacks access to the parent collection", 403)
        overwrite = request.headers.get("Overwrite", "T").upper()
        if overwrite not in {"T", "F"}:
            return Response("Overwrite must be T or F", 400)
        depth = request.headers.get("Depth", "infinity").lower()
        if request.method == "COPY" and is_collection and depth not in {"0", "infinity"}:
            return Response("collection COPY requires Depth: 0 or infinity", 400)
        if request.method == "MOVE" and is_collection and depth != "infinity":
            return Response("collection MOVE requires Depth: infinity", 400)
        precondition_error = _http_precondition_error(username, resource, document)
        if precondition_error is not None:
            return precondition_error
        current_etag = _etag(document) if document is not None else ""
        try:
            destination, destination_relative = _destination(username, identity)
        except PermissionError:
            return Response("destination is outside the authenticated WebDAV tree", 502)
        except ValueError as exc:
            return Response(str(exc), 400)
        if not _vfs().allows(username, destination.parent, "write"):
            return _need_privileges_response(
                request.path, _missing_method_privilege(request.method), allow,
            )
        if destination.exists() and not _vfs().allows(username, destination, "write"):
            return _need_privileges_response(
                request.path, _missing_method_privilege(request.method), allow,
            )
        name_error = _portable_name_error(
            username,
            destination,
            exclude=resource if request.method == "MOVE" else None,
        )
        if name_error is not None:
            return name_error
        replacing_document = None
        if destination == resource:
            status = 403 if request.method == "MOVE" else 412
            return Response("source and destination are the same resource", status)
        if destination.exists():
            if overwrite == "F":
                return Response("destination exists and Overwrite is F", 412)
            if is_collection or destination.is_symlink() or not destination.is_file():
                return Response("only an existing regular file can be replaced by COPY or MOVE", 412)
            try:
                replacing_document = _tree_document(destination)
            except ValueError:
                return Response("destination is not an available managed document", 409)
            destination_key = _lock_key(destination, replacing_document)
            destination_etag = _etag(replacing_document)
            if not _request_token(destination_key) and not _request_etag(destination_key, destination_etag):
                return Response(
                    "replacing an existing destination requires its tagged DAV If ETag or lock token",
                    428, {"ETag": destination_etag, "Cache-Control": "private, no-cache"},
                )
            destination_lock = _require_lock(destination, replacing_document, username)
            if destination_lock is not None:
                destination_lock.headers.update({"ETag": destination_etag, "Cache-Control": "private, no-cache"})
                return destination_lock
        if not destination.parent.is_dir():
            return Response("destination parent does not exist", 409)
        if replacing_document is None:
            destination_lock = _require_lock(destination, None, username)
            if destination_lock is not None:
                return destination_lock
        manifest = None
        if is_collection:
            if request.method == "MOVE":
                collection_lock_error = _collection_lock_error(resource, username)
                if collection_lock_error is not None:
                    return collection_lock_error
            try:
                manifest = _store().collection_manifest(
                    _store().relative(resource), f"webdav:{username}",
                    depth=depth if request.method == "COPY" else "infinity",
                )
            except ValueError as exc:
                status = 507 if "too many" in str(exc) or "nesting depth" in str(exc) else 423 if "locked" in str(exc) or "staged" in str(exc) else 409
                return Response(str(exc), status)
        if request.method == "COPY":
            if replacing_document is not None:
                growth = 0
            elif manifest is not None and depth == "infinity":
                growth = int(manifest["total_bytes"])
            else:
                growth = resource.stat().st_size if document else 0
            quota_error = _check_quota(username, "COPY", destination, growth)
            if quota_error is not None:
                return quota_error
        try:
            if is_collection and request.method == "COPY":
                result = _store().copy_collection(
                    _store().relative(resource), destination_relative, f"webdav:{username}", depth=depth,
                )
                copied_directories = result["directories_relative"]
                for nested in copied_directories:
                    source_collection = resource if nested == Path(".") else resource / nested
                    destination_collection = destination if nested == Path(".") else destination / nested
                    _copy_dead_properties(username, source_collection, None, destination_collection, None)
                for item in result["resources"]:
                    _copy_dead_properties(
                        username, item["source"], item["source_document"],
                        item["destination"], item["destination_document"],
                    )
            elif is_collection:
                result = _store().move_collection(
                    _store().relative(resource), destination_relative, f"webdav:{username}",
                )
                _release_collection_locks_after_move(username, resource)
            elif request.method == "COPY" and replacing_document is not None:
                result = _store().replace_document_via_copy(
                    document["document_id"], replacing_document["document_id"], f"webdav:{username}",
                    expected_source_sha256=_etag_value(current_etag),
                    expected_destination_sha256=_etag_value(destination_etag),
                    max_bytes=int(current_app.config["MAX_CONTENT_LENGTH"]),
                )
            elif request.method == "COPY":
                result = _store().copy_document(document["document_id"], destination_relative, f"webdav:{username}")
                _copy_dead_properties(username, resource, document, destination, result)
            elif replacing_document is not None:
                replacement = _store().replace_document_via_move(
                    document["document_id"], replacing_document["document_id"], f"webdav:{username}",
                    expected_source_sha256=_etag_value(current_etag),
                    expected_destination_sha256=_etag_value(destination_etag),
                    max_bytes=int(current_app.config["MAX_CONTENT_LENGTH"]),
                )
                result = replacement["document"]
            else:
                with exclusive_file_lock(_store().control / ".document-content.lock"):
                    result = _store().move_document(document["document_id"], _store().relative(destination.parent), f"webdav:{username}", destination_name=destination.name)
        except OSError:
            return _quota_error(username, request.method, destination, 0, "sufficient-disk-space")
        except (FileExistsError, RuntimeError, ValueError) as exc:
            message = str(exc)
            status = 507 if "too many" in message or "nesting depth" in message or "rollback failed" in message else 413 if "upload size limit" in message else 412 if "changed since" in message else 423 if "locked" in message or "staged" in message else 403 if "itself" in message else 409
            return Response(str(exc), status)
        changed_paths: list[str] = []
        if is_collection:
            for nested in result["directories_relative"]:
                destination_member = destination if nested == Path(".") else destination / nested
                changed_paths.append(_store().relative(destination_member))
                if request.method == "MOVE":
                    source_member = resource if nested == Path(".") else resource / nested
                    changed_paths.append(_store().relative(source_member))
            for item in result["resources"]:
                if request.method == "COPY":
                    changed_paths.append(_store().relative(item["destination"]))
                else:
                    changed_paths.extend([item["before"], item["after"]])
        if request.method == "COPY":
            _record_sync_changes(username, *(changed_paths or [destination_relative]))
        else:
            _record_sync_changes(username, *(changed_paths or [_store().relative(resource), destination_relative]))
            if not is_collection:
                _release_file_lock_after_move(username, resource, document)
        headers = {"Location": _tree_url(username, destination_relative, collection=is_collection)}
        if not is_collection:
            headers.update(_stored_integrity_headers(result))
            headers["Location"] = _tree_url(username, destination_relative)
            headers["Content-Location"] = headers["Location"]
        return Response("", 204 if replacing_document is not None else 201, headers)

    return Response("method not allowed", 405, {"Allow": allow})


@bp.route(
    "/webdav/principals/<username>/",
    defaults={"principal_id": ""},
    methods=["OPTIONS", "PROPFIND"],
)
@bp.route(
    "/webdav/principals/<username>/<principal_id>",
    methods=["OPTIONS", "PROPFIND"],
)
def principal_resource(username: str, principal_id: str):
    """Expose only the authenticated user's stable, read-only principal resource."""
    identity = _authenticate()
    if identity is None:
        return _unauthorized()
    if identity["username"] != username or principal_id not in {"", "self"}:
        return Response("not found", 404)
    g._webdav_identity = identity
    if request.method == "OPTIONS":
        return Response("", 204, {
            "DAV": "1", "Allow": "OPTIONS, PROPFIND",
            "Cache-Control": "private, no-store",
        })
    depth = request.headers.get("Depth", "0")
    if depth not in {"0", "1"}:
        return Response("principal PROPFIND requires Depth: 0 or 1", 403)
    try:
        query = _parse_propfind(request.get_data(cache=True))
    except OverflowError as exc:
        return Response(str(exc), 413)
    except PermissionError:
        error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:no-external-entities/></d:error>'
        return Response(error, 400, mimetype="application/xml")
    except ValueError as exc:
        return Response(str(exc), 400)
    collection = principal_id == ""
    href = _principal_url(username, collection=collection)
    responses = [
        _principal_prop_response(
            href, username, collection=collection, query=query,
        )
    ]
    if collection and depth == "1":
        principal = _principal_url(username)
        responses.append(
            _principal_prop_response(
                principal, username, collection=False, query=query,
            )
        )
    xml = f'<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:">{"".join(responses)}</d:multistatus>'
    return Response(
        xml,
        207,
        {
            "Content-Type": "application/xml; charset=utf-8",
            "Cache-Control": "private, no-store",
            "Vary": "Authorization, Depth",
        },
    )


@bp.route("/webdav/", defaults={"path": ""}, methods=["OPTIONS", "PROPFIND"])
@bp.route("/webdav/<path:path>", methods=["OPTIONS", "PROPFIND", "PROPPATCH", "GET", "HEAD", "PUT", "LOCK", "UNLOCK"])
def endpoint(path: str):
    identity = _authenticate()
    if identity is None:
        return _unauthorized()
    username = identity["username"]
    g._webdav_identity = identity
    allow = "OPTIONS, PROPFIND, GET, HEAD" if identity["scope"] == "read" else "OPTIONS, PROPFIND, PROPPATCH, GET, HEAD, PUT, LOCK, UNLOCK"
    if request.method == "OPTIONS":
        return Response("", 204, {
            "DAV": "1, 2", "MS-Author-Via": "DAV", "Allow": allow,
            "Want-Content-Digest": DIGEST_PREFERENCE,
        })
    if identity["scope"] != "write" and request.method in WRITE_METHODS:
        return _need_privileges_response(
            request.path, _missing_method_privilege(request.method), allow,
        )

    parts = [part for part in path.split("/") if part]
    if any(part in {".", ".."} for part in parts) or (len(parts) >= 2 and parts[1] != username):
        return Response("not found", 404)
    document = None
    if len(parts) == 3 and parts[0] == "documents" and "--" in parts[2]:
        document_id, requested_name = parts[2].split("--", 1)
        try:
            document = _store().get_document(document_id)
            document_path = _document_path(document)
        except ValueError:
            return Response("not found", 404)
        if requested_name != document_path.name:
            return Response("not found", 404)
        if not _credential_allows_path(identity, document_path):
            return Response("not found", 404)
        if not _vfs().allows(username, document_path, "read"):
            return Response("not found", 404)

    if request.method == "PROPFIND":
        depth = request.headers.get("Depth", "0")
        if depth not in {"0", "1"}:
            return Response("finite Depth required", 403)
        try:
            query = _parse_propfind(request.get_data(cache=True))
        except OverflowError as exc:
            return Response(str(exc), 413)
        except PermissionError:
            error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:no-external-entities/></d:error>'
            return Response(error, 400, mimetype="application/xml")
        except ValueError as exc:
            return Response(str(exc), 400)
        responses: list[str] = []
        if not parts:
            responses.append(_prop_response(request.path, "SimpleOffice4Me", collection=True, query=query))
        elif parts == ["documents", username]:
            responses.append(_prop_response(request.path, "SimpleOffice Dokumente", collection=True, username=username, resource=_store().root, query=query))
            if depth == "1":
                for item in _store().list_documents():
                    try:
                        item_path = _document_path(item)
                    except ValueError:
                        continue
                    if not _credential_allows_path(identity, item_path):
                        continue
                    if not _vfs().allows(username, item_path, "read"):
                        continue
                    responses.append(_prop_response(_resource_url(username, item), Path(item["last_path"]).name, document=item, username=username, resource=item_path, query=query))
        elif document is not None:
            responses.append(_prop_response(request.path, document_path.name, document=document, username=username, resource=document_path, query=query))
        else:
            return Response("not found", 404)
        return Response(f'<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:">{"".join(responses)}</d:multistatus>', 207, mimetype="application/xml")

    if document is None:
        return Response("not found", 404)
    if request.method in WRITE_METHODS and not _vfs().allows(username, document_path, "write"):
        return _need_privileges_response(request.path, _missing_method_privilege(request.method), allow)
    if request.method in WRITE_METHODS:
        mutation_lock = exclusive_file_lock(_sync_path().with_suffix(".mutation.lock"))
        mutation_lock.__enter__()
        g._webdav_mutation_lock = mutation_lock
        if_error = _if_header_error(username, identity)
        if if_error is not None:
            return if_error
    if request.method == "PROPPATCH":
        lock_error = _require_lock(document_path, document, username)
        if lock_error is not None:
            return lock_error
        precondition_error = _http_precondition_error(username, document_path, document)
        if precondition_error is not None:
            return precondition_error
        try:
            _store()._require_document_editable(document)
        except ValueError as exc:
            return Response(str(exc), 423)
        response, changed = _apply_proppatch(username, document_path, document, request.path)
        if changed:
            _record_sync_changes(username, _store().relative(document_path))
        return response
    current_etag = _etag(document)
    common_headers = {"ETag": current_etag, "Accept-Ranges": "bytes", "Cache-Control": "private, no-cache"}
    if request.method in {"GET", "HEAD"}:
        return _download_response(document_path, username, document, "application/octet-stream")
    if request.method == "LOCK":
        response = _lock_request(username, document_path, document, request.url)
        response.headers.update(common_headers)
        return response
    if request.method == "UNLOCK":
        token, token_error = _unlock_token()
        if token_error is not None:
            return token_error
        lock_path = _locks_path()
        with exclusive_file_lock(lock_path.with_suffix(".lock")):
            payload = _active_locks()
            existing = payload["locks"].get(document["document_id"])
            if not existing or existing.get("token") != token or existing.get("username") != username:
                return Response("lock token does not match", 409)
            payload["locks"].pop(document["document_id"], None)
            atomic_json_write(lock_path, payload)
        _record_lock_audit("webdav_lock_released", username, document_path, existing)
        return Response("", 204)
    if request.method == "PUT":
        lock_error = _require_lock(document_path, document, username)
        if lock_error is not None:
            lock_error.headers.update(common_headers)
            return lock_error
        precondition_error = _http_precondition_error(username, document_path, document)
        if precondition_error is not None:
            return precondition_error
        content = request.get_data()
        digest_error = _verify_content_digest(content, username, document_path)
        if digest_error is not None:
            return digest_error
        quota_error = _check_quota(username, "PUT", document_path, len(content) - document_path.stat().st_size)
        if quota_error is not None:
            return quota_error
        scan_error = _webdav_upload_scan_error(content, username, document_path)
        if scan_error is not None:
            return scan_error
        try:
            updated = _store().replace_content(
                document["document_id"], content, f"webdav:{username}",
                expected_sha256=_etag_value(current_etag), max_bytes=int(current_app.config["MAX_CONTENT_LENGTH"]),
            )
        except ValueError as exc:
            message = str(exc)
            status = 412 if "changed since" in message else 423 if "locked" in message or "staged" in message else 400
            return Response(str(exc), status, common_headers)
        return Response("", 204, _stored_integrity_headers(updated))
    return Response("method not allowed", 405)

# Export private helpers too so later ordered parts see the same globals
# they had in the original single module.
__all__ = [name for name in globals() if not name.startswith("__")]
