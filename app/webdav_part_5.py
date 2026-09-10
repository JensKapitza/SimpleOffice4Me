"""WebDAV implementation part 5 of 6."""
from __future__ import annotations

from .webdav_part_4 import *

def _release_file_lock_after_move(username: str, source: Path, document: dict) -> None:
    """Release an explicit source lock after MOVE while retaining target locks."""
    key = _lock_key(source, document)
    lock = _lock_for(key)
    if not lock or lock.get("username") != username:
        return
    _release_lock(key)
    _store().history.record(
        "webdav_lock_released_by_move", f"webdav:{username}", "webdav-locks",
        hashlib.sha256(f"{username}:{lock.get('resource', '')}".encode()).hexdigest(),
        {
            "resource": lock.get("resource", ""), "token": lock.get("token", ""),
            "released_at": utc_now(), "actor": f"webdav:{username}",
        },
    )


def _release_collection_locks_after_delete(username: str, source: Path) -> None:
    """Destroy every lock rooted on a successfully deleted collection member."""
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
            "webdav_lock_destroyed_by_delete", f"webdav:{username}", "webdav-locks",
            hashlib.sha256(f"{username}:{lock.get('resource', '')}".encode()).hexdigest(),
            {
                "resource": str(lock.get("resource", "")), "depth": str(lock.get("depth", "0")),
                "deleted_at": utc_now(), "actor": f"webdav:{username}",
            },
        )


def _visible_snapshot(collection: Path) -> dict[str, dict]:
    """Return the visible regular-file tree without following unsafe nodes."""
    store = _store()
    snapshot: dict[str, dict] = {}
    for current, directories, files in os.walk(collection, followlinks=False):
        parent = Path(current)
        directories[:] = sorted(
            name for name in directories
            if name not in {CONTROL_DIR, HISTORY_DIR} and not (parent / name).is_symlink()
        )
        for name in directories:
            path = parent / name
            snapshot[store.relative(path)] = {"collection": True, "signature": "collection"}
        for name in sorted(files):
            if name == POLICY_FILE:
                continue
            path = parent / name
            if path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
            snapshot[store.relative(path)] = {
                "collection": False,
                "signature": f"{stat.st_size}:{stat.st_mtime_ns}",
            }
    return snapshot


def _new_sync_token() -> str:
    return f"urn:uuid:{uuid.uuid4()}"


def _record_sync_changes(username: str, *relative_paths: str) -> None:
    """Record mutations immediately, including remove-and-remap sequences."""
    path = _sync_path()
    if not path.exists():
        return
    normalized = sorted({value.strip("/") for value in relative_paths if value.strip("/")})
    if not normalized:
        return
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _read_json(path, {"version": 1, "users": {}})
        collections = payload.get("users", {}).get(username, {}).get("collections", {})
        if not isinstance(collections, dict):
            return
        for collection_key, state in collections.items():
            if not isinstance(state, dict):
                continue
            collection_relative = "" if collection_key == "." else str(collection_key)
            previous = state.get("snapshot", {}) if isinstance(state.get("snapshot"), dict) else {}
            current = dict(previous)
            for relative in normalized:
                resource = _store().root / relative
                if resource.is_dir() and not resource.is_symlink():
                    current[relative] = {"collection": True, "signature": "collection"}
                elif resource.is_file() and not resource.is_symlink():
                    stat = resource.stat()
                    current[relative] = {"collection": False, "signature": f"{stat.st_size}:{stat.st_mtime_ns}"}
                else:
                    current.pop(relative, None)
            changes = state.get("changes", []) if isinstance(state.get("changes"), list) else []
            tokens = state.get("tokens", []) if isinstance(state.get("tokens"), list) else []
            revision = int(state.get("revision", 0))
            path_revisions = state.get("path_revisions")
            if not isinstance(path_revisions, dict):
                path_revisions = {}
                for relative, info in sorted(previous.items()):
                    revision += 1
                    changes.append({
                        "revision": revision, "path": relative, "removed": False,
                        "collection": bool(info.get("collection")),
                    })
                    path_revisions[relative] = revision
            for relative in normalized:
                try:
                    Path(relative).relative_to(collection_relative) if collection_relative else Path(relative)
                except ValueError:
                    continue
                revision += 1
                token = _new_sync_token()
                changes.append({
                    "revision": revision,
                    "path": relative,
                    "removed": relative not in current,
                    "collection": bool((current.get(relative) or previous.get(relative) or {}).get("collection")),
                })
                if relative in current:
                    path_revisions[relative] = revision
                else:
                    path_revisions.pop(relative, None)
                tokens.append({"token": token, "revision": revision})
                state["token"] = token
            state["revision"] = revision
            state["snapshot"] = current
            state["path_revisions"] = path_revisions
            state["changes"] = changes[-MAX_SYNC_CHANGES:]
            minimum = state["changes"][0]["revision"] - 1 if state["changes"] else revision
            state["tokens"] = [item for item in tokens if int(item.get("revision", -1)) >= minimum][-MAX_SYNC_TOKENS:]
        atomic_json_write(path, payload)


def _sync_state(username: str, collection: Path) -> dict:
    """Reconcile one user-bound collection journal with the current disk tree."""
    path = _sync_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    collection_key = _store().relative(collection) or "."
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _read_json(path, {"version": 1, "users": {}})
        users = payload.setdefault("users", {})
        collections = users.setdefault(username, {}).setdefault("collections", {})
        snapshot = _visible_snapshot(collection)
        state = collections.get(collection_key)
        if not isinstance(state, dict):
            changes = []
            path_revisions = {}
            revision = 0
            for relative, info in sorted(snapshot.items()):
                revision += 1
                changes.append({
                    "revision": revision,
                    "path": relative,
                    "removed": False,
                    "collection": bool(info.get("collection")),
                })
                path_revisions[relative] = revision
            token = _new_sync_token()
            state = {
                "revision": revision,
                "token": token,
                "tokens": [{"token": token, "revision": revision}],
                "changes": changes,
                "snapshot": snapshot,
                "path_revisions": path_revisions,
            }
            collections[collection_key] = state
        else:
            previous = state.get("snapshot", {}) if isinstance(state.get("snapshot"), dict) else {}
            changes = state.get("changes", []) if isinstance(state.get("changes"), list) else []
            tokens = state.get("tokens", []) if isinstance(state.get("tokens"), list) else []
            revision = int(state.get("revision", 0))
            path_revisions = state.get("path_revisions")
            if not isinstance(path_revisions, dict):
                # A one-time, transport-only baseline makes pre-pagination
                # journals safely resumable. Existing clients merely observe
                # each current URL once more; document data is untouched.
                path_revisions = {}
                for relative, info in sorted(snapshot.items()):
                    revision += 1
                    changes.append({
                        "revision": revision,
                        "path": relative,
                        "removed": False,
                        "collection": bool(info.get("collection")),
                    })
                    path_revisions[relative] = revision
                if snapshot:
                    token = _new_sync_token()
                    tokens.append({"token": token, "revision": revision})
                    state["token"] = token
            for relative in sorted(set(previous) | set(snapshot)):
                if previous.get(relative) == snapshot.get(relative):
                    continue
                revision += 1
                token = _new_sync_token()
                changes.append({
                    "revision": revision,
                    "path": relative,
                    "removed": relative not in snapshot,
                    "collection": bool((snapshot.get(relative) or previous.get(relative) or {}).get("collection")),
                })
                if relative in snapshot:
                    path_revisions[relative] = revision
                else:
                    path_revisions.pop(relative, None)
                tokens.append({"token": token, "revision": revision})
                state["token"] = token
            state["revision"] = revision
            state["snapshot"] = snapshot
            state["path_revisions"] = path_revisions
            state["changes"] = changes[-MAX_SYNC_CHANGES:]
            minimum = state["changes"][0]["revision"] - 1 if state["changes"] else revision
            retained = [item for item in tokens if int(item.get("revision", -1)) >= minimum]
            state["tokens"] = retained[-MAX_SYNC_TOKENS:]
            if not state.get("token"):
                state["token"] = state["tokens"][-1]["token"] if state["tokens"] else _new_sync_token()
        payload["version"] = 1
        atomic_json_write(path, payload)
        return json.loads(json.dumps(state))


def _sync_token_for_revision(username: str, collection: Path, revision: int) -> str:
    """Return a persisted opaque token for an exactly processed revision."""
    path = _sync_path()
    collection_key = _store().relative(collection) or "."
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _read_json(path, {"version": 1, "users": {}})
        state = payload.get("users", {}).get(username, {}).get("collections", {}).get(collection_key)
        if not isinstance(state, dict):
            raise ValueError("sync state is unavailable")
        current_revision = int(state.get("revision", 0))
        if revision < 0 or revision > current_revision:
            raise ValueError("sync revision is unavailable")
        changes = state.get("changes", []) if isinstance(state.get("changes"), list) else []
        minimum = int(changes[0].get("revision", 0)) - 1 if changes else current_revision
        if revision < minimum:
            raise ValueError("sync revision has expired")
        tokens = state.get("tokens", []) if isinstance(state.get("tokens"), list) else []
        for item in reversed(tokens):
            if isinstance(item, dict) and int(item.get("revision", -1)) == revision:
                return str(item["token"])
        token = _new_sync_token()
        tokens.append({"token": token, "revision": revision})
        state["tokens"] = tokens[-MAX_SYNC_TOKENS:]
        atomic_json_write(path, payload)
        return token


def _collection_sync_token(username: str, collection: Path) -> str:
    """Read the mutation-maintained token without rescanning on every PROPFIND."""
    collection_key = _store().relative(collection) or "."
    state = (
        _read_json(_sync_path(), {"users": {}})
        .get("users", {}).get(username, {}).get("collections", {}).get(collection_key)
    )
    if isinstance(state, dict) and state.get("token"):
        return str(state["token"])
    return str(_sync_state(username, collection)["token"])


def _sync_if_error(username: str, identity: dict) -> Response | None:
    """Validate all RFC 4918 If conditions, including RFC 6578 tokens."""
    return _if_header_error(username, identity)


def _sync_member_in_scope(relative: str, collection: Path, level: str) -> bool:
    collection_relative = _store().relative(collection)
    try:
        nested = Path(relative).relative_to(collection_relative) if collection_relative else Path(relative)
    except ValueError:
        return False
    return nested != Path(".") and (level == "infinite" or len(nested.parts) == 1)


def _sync_limit(root: ElementTree.Element) -> int:
    nodes = root.findall(f"{{{DAV}}}limit")
    if not nodes:
        return MAX_SYNC_PAGE_RESULTS
    if len(nodes) != 1:
        raise ValueError("sync-collection accepts one limit")
    results = nodes[0].findall(f"{{{DAV}}}nresults")
    if len(results) != 1 or len(nodes[0]) != 1:
        raise ValueError("DAV:limit requires one DAV:nresults")
    value = (results[0].text or "").strip()
    if not value.isdecimal() or int(value) < 1:
        raise ValueError("DAV:nresults must be a positive integer")
    return min(int(value), MAX_SYNC_PAGE_RESULTS)


def _effective_sync_members(members: list[dict], level: str) -> list[dict]:
    """Suppress descendant tombstones while advancing their sync cursor."""
    removed_collections: dict[str, dict] = {}
    effective: list[dict] = []
    for original in sorted(members, key=lambda item: (int(item.get("revision", 0)), str(item.get("path", "")))):
        item = dict(original)
        relative = str(item["path"])
        revision = int(item.get("revision", 0))
        parent = next(
            (
                removed for path, removed in removed_collections.items()
                if level == "infinite" and relative.startswith(path + "/")
            ),
            None,
        )
        if parent is not None:
            parent["cursor_revision"] = max(int(parent.get("cursor_revision", 0)), revision)
            continue
        item["cursor_revision"] = revision
        effective.append(item)
        if item.get("removed") and item.get("collection"):
            removed_collections[relative] = item
    return effective


def _sync_member_response(username: str, item: dict, query: tuple[str, list[str]]) -> str:
    relative = str(item["path"])
    resource = _store().root / relative
    current_collection = resource.is_dir() and not resource.is_symlink()
    current_file = resource.is_file() and not resource.is_symlink()
    href = _tree_url(
        username, relative,
        collection=current_collection or (not current_file and bool(item.get("collection"))),
    )
    if item.get("removed") or (not current_collection and not current_file):
        return f"<d:response><d:href>{escape(href)}</d:href><d:status>HTTP/1.1 404 Not Found</d:status></d:response>"
    if current_collection:
        return _prop_response(
            href, resource.name, collection=True, username=username,
            resource=resource, query=query, searchable=True,
        )
    try:
        document = _tree_document(resource)
    except ValueError:
        return f"<d:response><d:href>{escape(href)}</d:href><d:status>HTTP/1.1 404 Not Found</d:status></d:response>"
    return _prop_response(
        href, resource.name, document=document, username=username,
        resource=resource, query=query, searchable=True,
    )


def _sync_limit_response(username: str, collection: Path, responses: list[str], token: str) -> Response:
    href = _tree_url(username, _store().relative(collection), collection=True)
    responses.append(
        f"<d:response><d:href>{escape(href)}</d:href>"
        "<d:status>HTTP/1.1 507 Insufficient Storage</d:status>"
        "<d:error><d:number-of-matches-within-limits/></d:error></d:response>"
    )
    xml = f'''<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:">{"".join(responses)}<d:sync-token>{escape(token)}</d:sync-token></d:multistatus>'''
    return Response(
        xml, 207, {
            "Content-Type": "application/xml; charset=utf-8",
            "Cache-Control": "private, no-store",
            "Vary": "Authorization, Depth",
            "X-SimpleOffice-Sync-Limit": "result-count",
        },
    )


def _sync_report(username: str, collection: Path) -> Response:
    if request.headers.get("Depth", "0") != "0":
        return Response("sync-collection requires Depth: 0", 400)
    body = request.get_data(cache=True)
    if len(body) > 64 * 1024:
        return Response("REPORT body is too large", 413)
    try:
        root = _safe_xml_root(body, f"{{{DAV}}}sync-collection")
    except OverflowError as exc:
        return Response(str(exc), 413)
    except PermissionError:
        error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:no-external-entities/></d:error>'
        return Response(error, 400, mimetype="application/xml")
    except ValueError:
        return Response("invalid sync-collection XML", 400)
    token_nodes = root.findall(f"{{{DAV}}}sync-token")
    level_nodes = root.findall(f"{{{DAV}}}sync-level")
    prop_nodes = root.findall(f"{{{DAV}}}prop")
    if len(token_nodes) != 1 or len(level_nodes) != 1 or len(prop_nodes) != 1:
        return Response("sync-token, sync-level and prop are required", 400)
    token_node, level_node = token_nodes[0], level_nodes[0]
    query = ("prop", [child.tag for child in prop_nodes[0]])
    level = (level_node.text or "").strip()
    if level not in {"1", "infinite"}:
        return Response("sync-level must be 1 or infinite", 400)
    try:
        page_limit = _sync_limit(root)
    except ValueError as exc:
        return Response(str(exc), 400)

    supplied_token = (token_node.text or "").strip()
    state = _sync_state(username, collection)
    if supplied_token:
        token_revisions = {
            str(item.get("token", "")): int(item.get("revision", -1))
            for item in state.get("tokens", []) if isinstance(item, dict)
        }
        if supplied_token not in token_revisions:
            error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:valid-sync-token/></d:error>'
            return Response(error, 403, mimetype="application/xml")
        since = token_revisions[supplied_token]
        latest: dict[str, dict] = {}
        for change in state.get("changes", []):
            if int(change.get("revision", -1)) > since and _sync_member_in_scope(str(change.get("path", "")), collection, level):
                latest[str(change["path"])] = change
        members = list(latest.values())
    else:
        path_revisions = state.get("path_revisions", {})
        members = [
            {
                "path": relative, "removed": False,
                "collection": bool(info.get("collection")),
                "revision": int(path_revisions.get(relative, state.get("revision", 0))),
            }
            for relative, info in sorted(state.get("snapshot", {}).items())
            if _sync_member_in_scope(relative, collection, level)
        ]
    members = _effective_sync_members(members, level)
    # A sync token is scoped to the user, but an ACL can change independently
    # of the change journal. Never disclose a path that is currently hidden.
    members = [
        item for item in members
        if _vfs().allows(username, _store().root / str(item.get("path", "")), "read")
    ]
    responses: list[str] = []
    consumed = 0
    response_size = 256
    for item in members[:page_limit]:
        rendered = _sync_member_response(username, item, query)
        rendered_size = len(rendered.encode("utf-8"))
        if responses and response_size + rendered_size > MAX_PROPFIND_RESPONSE_BYTES:
            break
        if not responses and response_size + rendered_size > MAX_PROPFIND_RESPONSE_BYTES:
            error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:number-of-matches-within-limits/></d:error>'
            return Response(error, 507, mimetype="application/xml")
        responses.append(rendered)
        response_size += rendered_size
        consumed += 1
    if consumed < len(members):
        cursor_revision = int(members[consumed - 1]["cursor_revision"])
        try:
            partial_token = _sync_token_for_revision(username, collection, cursor_revision)
        except ValueError:
            error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:valid-sync-token/></d:error>'
            return Response(error, 403, mimetype="application/xml")
        _store().history.record(
            "webdav_sync_truncated", f"webdav:{username}", "webdav-sync",
            hashlib.sha256(f"{username}:{partial_token}".encode()).hexdigest(),
            {
                "collection": _store().relative(collection) or ".", "sync_level": level,
                "returned": consumed, "remaining": len(members) - consumed,
                "cursor_revision": cursor_revision, "actor": f"webdav:{username}",
                "at": utc_now(),
            },
        )
        return _sync_limit_response(username, collection, responses, partial_token)
    xml = f'''<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:">{"".join(responses)}<d:sync-token>{escape(str(state["token"]))}</d:sync-token></d:multistatus>'''
    return Response(
        xml, 207, {
            "Content-Type": "application/xml; charset=utf-8",
            "Cache-Control": "private, no-store", "Vary": "Authorization, Depth",
        },
    )


@bp.route("/documents/<document_id>/libreoffice", methods=["GET", "POST"])
@login_required
def setup_document(document_id: str):
    try:
        document = _store().get_document(document_id)
        _document_path(document)
    except ValueError:
        return Response("document not found", 404)
    username = str(g.user["username"])
    generated_password = ""
    if request.method == "POST":
        action = request.form.get("action", "activate")
        if action == "revoke_all":
            revoke(username, username)
            flash("Alle WebDAV-Zugänge wurden widerrufen.")
        elif action == "revoke":
            if revoke(username, username, request.form.get("credential_id", "")):
                flash("WebDAV-Zugang wurde widerrufen.")
            else:
                flash("Der WebDAV-Zugang war bereits widerrufen.")
        else:
            try:
                generated_password = activate(
                    username, username,
                    label=request.form.get("label", "Desktop-Zugang"),
                    scope=request.form.get("scope", "write"),
                    expires_days=int(request.form.get("expires_days", "90")),
                    path_prefix=request.form.get("path_prefix", ""),
                )
            except (TypeError, ValueError) as exc:
                flash(str(exc) or "WebDAV-Zugang konnte nicht angelegt werden.")
    credentials = credentials_for(username)
    for credential in credentials:
        credential["webdav_url"] = _tree_url(
            username, credential["path_prefix"], external=True, collection=True,
        )
    configured = any(not item["expired"] for item in credentials)
    webdav_url = _resource_url(username, document, external=True)
    webdav_root_url = next(
        (item["webdav_url"] for item in credentials if not item["expired"]),
        _tree_url(username, external=True, collection=True),
    )
    return render_template(
        "documents/libreoffice.html",
        document=document,
        webdav_url=webdav_url,
        webdav_root_url=webdav_root_url,
        configured=configured,
        generated_password=generated_password,
        credentials=credentials,
        default_path_prefix="" if Path(str(document["last_path"])).parent == Path(".") else str(Path(str(document["last_path"])).parent),
        quota=_quota_state(),
    )


@bp.route("/settings/webdav", methods=["GET", "POST"])
@login_required
def setup_user_webdav():
    """Manage one user's persistent, whole-tree WebDAV credentials."""
    username = str(g.user["username"])
    access_vfs, users, access_administrator = _document_access_context(username)
    generated_password = ""
    if request.method == "POST":
        action = request.form.get("action", "activate")
        if action == "revoke_all":
            revoke(username, username); flash("Alle WebDAV-Zugänge wurden widerrufen.")
        elif action == "revoke":
            flash("WebDAV-Zugang wurde widerrufen." if revoke(username, username, request.form.get("credential_id", "")) else "Der WebDAV-Zugang war bereits widerrufen.")
        elif action == "rotate":
            try:
                if request.form.get("confirm_rotation") != "ROTATE":
                    raise ValueError("Bitte die sofortige Ungültigkeit des alten Passworts bestätigen.")
                generated_password = rotate(username, username, request.form.get("credential_id", ""), int(request.form.get("expires_days", "365")))
                flash("App-Passwort ersetzt. Das alte Passwort ist ab sofort ungültig.")
            except (TypeError, ValueError) as exc:
                flash(str(exc) or "WebDAV-Passwort konnte nicht ersetzt werden.")
        elif action == "save_access":
            folder = request.form.get("folder", ".")
            try:
                grants = {
                    candidate: request.form.get(f"access_{candidate}", "")
                    for candidate in users
                }
                access_vfs.set_grants(
                    folder, grants, username,
                    inherit=request.form.get("inherit") == "1",
                )
                flash("Ordnerrechte gespeichert. WebDAV und SFTP verwenden dieselbe Regel.")
            except (OSError, PermissionError, ValueError) as exc:
                flash(f"Ordnerrechte konnten nicht gespeichert werden: {exc}")
        elif action == "add_ssh_key":
            try:
                add_key(
                    current_app.config["DOCUMENT_ROOT"], username,
                    request.form.get("public_key", ""),
                    label=request.form.get("ssh_key_label", "SSHFS-Schlüssel"),
                    scope=request.form.get("ssh_key_scope", "write"),
                    expires_days=int(request.form.get("ssh_key_expires_days", "365")),
                    actor=username,
                )
                flash("SSH-Public-Key hinterlegt. Der private Schlüssel bleibt ausschließlich auf deinem Gerät.")
            except (OSError, PermissionError, TypeError, ValueError) as exc:
                flash(str(exc) or "SSH-Schlüssel konnte nicht hinterlegt werden.")
        elif action == "revoke_ssh_key":
            removed = revoke_key(
                current_app.config["DOCUMENT_ROOT"], username,
                request.form.get("ssh_key_id", ""), actor=username,
            )
            flash("SSH-Schlüssel widerrufen." if removed else "Der SSH-Schlüssel war bereits widerrufen.")
        else:
            try:
                generated_password = activate(username, username, label=request.form.get("label", "Allgemeiner Desktop-Zugang"), scope=request.form.get("scope", "write"), expires_days=int(request.form.get("expires_days", "365")), path_prefix="")
            except (TypeError, ValueError) as exc:
                flash(str(exc) or "WebDAV-Zugang konnte nicht angelegt werden.")
    credentials = credentials_for(username)
    for credential in credentials:
        credential["webdav_url"] = _tree_url(username, credential["path_prefix"], external=True, collection=True)
    folders = _access_folders(access_vfs, username, access_administrator)
    selected_folder = request.args.get("folder", request.form.get("folder", "."))
    if selected_folder not in folders:
        selected_folder = "."
    selected_policy = access_vfs.access_policy(selected_folder)
    return render_template(
        "documents/webdav_settings.html", username=username,
        webdav_root_url=_tree_url(username, external=True, collection=True),
        generated_password=generated_password, credentials=credentials,
        quota=_quota_state(), access_users=users, access_folders=folders,
        selected_folder=selected_folder, selected_policy=selected_policy,
        access_administrator=access_administrator,
        ssh_keys=keys_for(current_app.config["DOCUMENT_ROOT"], username),
    )

# Export private helpers too so later ordered parts see the same globals
# they had in the original single module.
__all__ = [name for name in globals() if not name.startswith("__")]
