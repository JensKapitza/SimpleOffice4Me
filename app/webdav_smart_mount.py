"""Virtual SmartView WebDAV mount backed by the original managed documents.

The overlay never duplicates document bytes. Virtual collection members resolve
back to the indexed original path and all file mutations are delegated to the
normal WebDAV implementation so ACLs, locks, ETags, revisions, quota and upload
scanning keep their existing semantics.
"""
from __future__ import annotations

import hashlib
import mimetypes
from collections import Counter
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

from flask import Blueprint, Response, current_app, request

from .resource_provider import ProviderError, ResourceEntry
from .resource_smartview import SMART_COLLECTIONS, SmartViewProvider
from .webdav import (
    _authenticate,
    _credential_allows_path,
    _etag,
    _tree_document,
    _tree_path,
    _unauthorized,
    _vfs,
    file_tree,
)

bp = Blueprint("webdav_smart_mount", __name__)
MAX_SMART_MEMBERS = 500
_READ_METHODS = {"GET", "HEAD", "PROPFIND", "OPTIONS"}
_DELEGATED_FILE_METHODS = {"GET", "HEAD", "PUT", "DELETE", "LOCK", "UNLOCK", "PROPPATCH"}


def _href(username: str, relative: str = "", *, collection: bool = False) -> str:
    parts = [quote(username, safe=""), *(quote(part, safe="") for part in relative.split("/") if part)]
    value = "/webdav/smart/" + "/".join(parts)
    if collection and not value.endswith("/"):
        value += "/"
    return value


def _virtual_name(entry: ResourceEntry, duplicate: bool) -> str:
    if not duplicate:
        return entry.name
    path = Path(entry.name)
    token = hashlib.sha256(entry.resource_id.encode("utf-8")).hexdigest()[:8]
    suffix = "".join(path.suffixes)
    stem = entry.name[: -len(suffix)] if suffix else entry.name
    return f"{stem} [{token}]{suffix}"


def _members(provider: SmartViewProvider, collection: str) -> list[tuple[str, ResourceEntry]]:
    entries = list(provider.list(collection))[:MAX_SMART_MEMBERS]
    counts = Counter(entry.name.casefold() for entry in entries)
    result = [
        (_virtual_name(entry, counts[entry.name.casefold()] > 1), entry)
        for entry in entries
    ]
    return sorted(result, key=lambda item: item[0].casefold())


def _visible(identity: dict, username: str, entry: ResourceEntry) -> bool:
    try:
        resource = _tree_path(entry.resource_id)
    except ValueError:
        return False
    return (
        _credential_allows_path(identity, resource)
        and _vfs().allows(username, resource, "read")
        and resource.is_file()
        and not resource.is_symlink()
    )


def _resolve_member(provider: SmartViewProvider, collection: str, virtual_name: str, identity: dict, username: str) -> ResourceEntry:
    for name, entry in _members(provider, collection):
        if name == virtual_name and _visible(identity, username, entry):
            return entry
    raise ProviderError("SmartView-Ressource nicht gefunden")


def _prop(name: str, href: str, *, collection: bool, size: int = 0, modified: str = "", etag: str = "", mime: str = "") -> str:
    resource_type = "<d:collection/>" if collection else ""
    length = "" if collection else f"<d:getcontentlength>{max(0, int(size))}</d:getcontentlength>"
    content_type = "" if collection else f"<d:getcontenttype>{escape(mime or 'application/octet-stream')}</d:getcontenttype>"
    etag_xml = f"<d:getetag>{escape(etag)}</d:getetag>" if etag else ""
    modified_xml = f"<d:getlastmodified>{escape(modified)}</d:getlastmodified>" if modified else ""
    return (
        f"<d:response><d:href>{escape(href)}</d:href><d:propstat><d:prop>"
        f"<d:displayname>{escape(name)}</d:displayname><d:resourcetype>{resource_type}</d:resourcetype>"
        f"{length}{content_type}{etag_xml}{modified_xml}"
        "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
    )


def _file_prop(username: str, collection: str, virtual_name: str, entry: ResourceEntry) -> str:
    resource = _tree_path(entry.resource_id)
    try:
        document = _tree_document(resource)
        etag = _etag(document)
    except ValueError:
        etag = ""
    mime = entry.mime_type or mimetypes.guess_type(entry.name)[0] or "application/octet-stream"
    return _prop(
        virtual_name,
        _href(username, f"{collection}/{virtual_name}"),
        collection=False,
        size=entry.size,
        modified=entry.modified,
        etag=etag,
        mime=mime,
    )


def _multistatus(items: list[str]) -> Response:
    xml = '<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:">' + "".join(items) + "</d:multistatus>"
    return Response(xml, 207, {"Content-Type": "application/xml; charset=utf-8", "Cache-Control": "private, no-store", "Vary": "Authorization, Depth"})


def _propfind(username: str, provider: SmartViewProvider, relative: str, identity: dict) -> Response:
    depth = request.headers.get("Depth", "1").casefold()
    if depth not in {"0", "1", "infinity"}:
        return Response("PROPFIND Depth must be 0, 1 or infinity", 400)
    parts = [part for part in relative.split("/") if part]
    if not parts:
        rows = [_prop("SmartView", _href(username, collection=True), collection=True)]
        if depth != "0":
            rows.extend(
                _prop(label, _href(username, key, collection=True), collection=True)
                for key, label in SMART_COLLECTIONS.items()
            )
        return _multistatus(rows)
    collection = parts[0]
    if collection not in SMART_COLLECTIONS:
        return Response("not found", 404)
    if len(parts) == 1:
        rows = [_prop(SMART_COLLECTIONS[collection], _href(username, collection, collection=True), collection=True)]
        if depth != "0":
            rows.extend(
                _file_prop(username, collection, name, entry)
                for name, entry in _members(provider, collection)
                if _visible(identity, username, entry)
            )
        return _multistatus(rows)
    if len(parts) != 2:
        return Response("not found", 404)
    try:
        entry = _resolve_member(provider, collection, parts[1], identity, username)
    except ProviderError:
        return Response("not found", 404)
    return _multistatus([_file_prop(username, collection, parts[1], entry)])


def _options(identity: dict) -> Response:
    allow = "OPTIONS, PROPFIND, GET, HEAD"
    if identity.get("scope") == "write":
        allow += ", PUT, DELETE, LOCK, UNLOCK, PROPPATCH"
    return Response("", 204, {
        "DAV": "1, 2",
        "MS-Author-Via": "DAV",
        "Allow": allow,
        "Cache-Control": "private, no-store",
        "Vary": "Authorization",
    })


@bp.route(
    "/webdav/smart/<username>/",
    defaults={"relative": ""},
    methods=["OPTIONS", "PROPFIND", "GET", "HEAD", "PUT", "DELETE", "MKCOL", "COPY", "MOVE", "LOCK", "UNLOCK", "PROPPATCH"],
)
@bp.route(
    "/webdav/smart/<username>/<path:relative>",
    methods=["OPTIONS", "PROPFIND", "GET", "HEAD", "PUT", "DELETE", "MKCOL", "COPY", "MOVE", "LOCK", "UNLOCK", "PROPPATCH"],
)
def smart_mount(username: str, relative: str):
    identity = _authenticate()
    if identity is None:
        return _unauthorized()
    if identity.get("username") != username:
        return Response("not found", 404)
    if request.method == "OPTIONS":
        return _options(identity)

    provider = SmartViewProvider(current_app.config["DOCUMENT_ROOT"])
    clean = str(relative or "").strip("/")
    if request.method == "PROPFIND":
        return _propfind(username, provider, clean, identity)

    parts = [part for part in clean.split("/") if part]
    if len(parts) != 2 or parts[0] not in SMART_COLLECTIONS:
        return Response("SmartView collections are virtual and cannot be changed structurally", 405)
    if request.method in {"MKCOL", "COPY", "MOVE"}:
        return Response("SmartView structure is virtual; use Resource Commander for copy or move", 405)
    if request.method not in _DELEGATED_FILE_METHODS:
        return Response("method not allowed", 405)
    if identity.get("scope") != "write" and request.method not in _READ_METHODS:
        return Response("write access required", 403)
    try:
        entry = _resolve_member(provider, parts[0], parts[1], identity, username)
    except ProviderError:
        return Response("not found", 404)

    return file_tree(username, entry.resource_id)


def init_app(app) -> None:
    app.register_blueprint(bp)
