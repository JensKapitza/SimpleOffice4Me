"""Bidirectional synchronization for an app-managed Google Drive folder."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from flask import current_app

from .db import get_db
from .document_store import DocumentStore
from .document_store_core import CONTROL_DIR, HISTORY_DIR, POLICY_FILE, PREVIEW_CACHE_DIR
from .google_drive_client import DriveClient, FOLDER_MIME, GOOGLE_NATIVE_PREFIX
from .google_tokens import GOOGLE_DRIVE_SCOPE, google_access_token

DRIVE_ROOT_NAME = "SimpleOffice4Me"
LOCAL_SYNC_FOLDER = "GoogleDrive"
VALID_DIRECTIONS = {"bidirectional", "upload_only", "download_only", "none"}
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_drive_name(value: str, drive_id: str = "") -> str:
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", str(value or "")).strip(" .")
    if not name:
        name = f"Drive-{str(drive_id or '')[:12] or 'Datei'}"
    stem = Path(name).stem.upper()
    if stem in WINDOWS_RESERVED:
        name = f"_{name}"
    if len(name) > 180:
        suffix = Path(name).suffix[:20]
        base_limit = max(1, 180 - len(suffix))
        name = f"{Path(name).stem[:base_limit]}{suffix}"
    return name


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sync_decision(
    local_sha256: str,
    last_local_sha256: str,
    remote_version: str,
    last_remote_version: str,
) -> str:
    local_changed = bool(last_local_sha256) and local_sha256 != last_local_sha256
    remote_changed = bool(last_remote_version) and remote_version != last_remote_version
    if local_changed and remote_changed:
        return "conflict"
    if local_changed:
        return "upload"
    if remote_changed:
        return "download"
    return "same"


def drive_max_bytes() -> int:
    requested = os.environ.get("SIMPLEOFFICE_GOOGLE_DRIVE_MAX_MIB", "128").strip()
    try:
        mib = int(requested)
    except ValueError:
        mib = 128
    mib = max(1, min(mib, 512))
    app_limit = int(current_app.config.get("MAX_CONTENT_LENGTH", mib * 1024 * 1024))
    return min(mib * 1024 * 1024, app_limit)


def ensure_drive_state(user_id: int) -> dict:
    db = get_db()
    db.execute(
        """INSERT INTO google_drive_state(user_id, root_folder_name, direction, enabled, updated_at)
           VALUES (?, ?, 'bidirectional', 1, CURRENT_TIMESTAMP)
           ON CONFLICT(user_id) DO NOTHING""",
        (int(user_id), DRIVE_ROOT_NAME),
    )
    db.commit()
    row = db.execute("SELECT * FROM google_drive_state WHERE user_id=?", (int(user_id),)).fetchone()
    return dict(row) if row else {}


def update_drive_settings(user_id: int, *, direction: str, enabled: bool) -> dict:
    selected = str(direction or "").strip()
    if selected not in VALID_DIRECTIONS:
        raise ValueError("invalid Google Drive sync direction")
    ensure_drive_state(user_id)
    db = get_db()
    db.execute(
        """UPDATE google_drive_state SET direction=?, enabled=?, updated_at=CURRENT_TIMESTAMP
           WHERE user_id=?""",
        (selected, int(bool(enabled)), int(user_id)),
    )
    db.commit()
    return ensure_drive_state(user_id)


def drive_links(user_id: int, limit: int = 100) -> list[dict]:
    rows = get_db().execute(
        """SELECT * FROM google_drive_link WHERE user_id=?
           ORDER BY COALESCE(last_sync_at, '') DESC, remote_name COLLATE NOCASE LIMIT ?""",
        (int(user_id), max(1, min(int(limit), 500))),
    ).fetchall()
    return [dict(row) for row in rows]


def _mapping_by_drive(db, user_id: int, drive_id: str):
    return db.execute(
        "SELECT * FROM google_drive_link WHERE user_id=? AND drive_file_id=?",
        (int(user_id), drive_id),
    ).fetchone()


def _mapping_by_local(db, user_id: int, relative_path: str):
    return db.execute(
        "SELECT * FROM google_drive_link WHERE user_id=? AND local_relative_path=?",
        (int(user_id), relative_path),
    ).fetchone()


def _mapping_parent_path(db, user_id: int, root_id: str, parents: list) -> str:
    for parent in parents or []:
        parent_id = str(parent or "").strip()
        if parent_id == root_id:
            return LOCAL_SYNC_FOLDER
        row = _mapping_by_drive(db, user_id, parent_id)
        if row and row["mime_type"] == FOLDER_MIME and row["local_relative_path"]:
            return str(row["local_relative_path"])
    return ""


def _unique_relative(store: DocumentStore, relative_path: str, drive_id: str) -> str:
    relative = PurePosixPath(relative_path)
    target = store.root / Path(*relative.parts)
    if not target.exists():
        return relative.as_posix()
    suffix = target.suffix
    stem = target.stem
    candidate = target.with_name(f"{stem} (Drive-{drive_id[:6]}){suffix}")
    counter = 2
    while candidate.exists():
        candidate = target.with_name(f"{stem} (Drive-{drive_id[:6]}-{counter}){suffix}")
        counter += 1
    return candidate.relative_to(store.root).as_posix()


def _ensure_local_folder(store: DocumentStore, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    target = store.root / Path(*relative.parts)
    target.mkdir(parents=True, exist_ok=True)
    current = store.root
    for part in relative.parts:
        current = current / part
        if current.is_dir():
            store.ensure_folder_policy(current)
    return target


def _upsert_link(db, user_id: int, drive_id: str, values: dict) -> None:
    existing = _mapping_by_drive(db, user_id, drive_id)
    merged = dict(existing) if existing else {}
    merged.update(values)
    merged.setdefault("document_id", "")
    merged.setdefault("local_relative_path", "")
    merged.setdefault("remote_name", "")
    merged.setdefault("mime_type", "application/octet-stream")
    merged.setdefault("remote_parent_id", "")
    merged.setdefault("remote_md5", "")
    merged.setdefault("remote_modified_at", "")
    merged.setdefault("remote_version", "")
    merged.setdefault("web_view_link", "")
    merged.setdefault("local_sha256", "")
    merged.setdefault("last_synced_local_sha256", "")
    merged.setdefault("last_synced_remote_version", "")
    merged.setdefault("status", "pending")
    merged.setdefault("last_error", "")
    merged["last_sync_at"] = values.get("last_sync_at", utc_now())
    db.execute(
        """INSERT INTO google_drive_link(
               user_id, drive_file_id, document_id, local_relative_path, remote_name,
               mime_type, remote_parent_id, remote_md5, remote_modified_at,
               remote_version, web_view_link, local_sha256,
               last_synced_local_sha256, last_synced_remote_version,
               status, last_sync_at, last_error
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id, drive_file_id) DO UPDATE SET
               document_id=excluded.document_id,
               local_relative_path=excluded.local_relative_path,
               remote_name=excluded.remote_name,
               mime_type=excluded.mime_type,
               remote_parent_id=excluded.remote_parent_id,
               remote_md5=excluded.remote_md5,
               remote_modified_at=excluded.remote_modified_at,
               remote_version=excluded.remote_version,
               web_view_link=excluded.web_view_link,
               local_sha256=excluded.local_sha256,
               last_synced_local_sha256=excluded.last_synced_local_sha256,
               last_synced_remote_version=excluded.last_synced_remote_version,
               status=excluded.status,
               last_sync_at=excluded.last_sync_at,
               last_error=excluded.last_error""",
        (
            int(user_id), drive_id, merged["document_id"], merged["local_relative_path"],
            merged["remote_name"], merged["mime_type"], merged["remote_parent_id"],
            merged["remote_md5"], merged["remote_modified_at"], merged["remote_version"],
            merged["web_view_link"], merged["local_sha256"],
            merged["last_synced_local_sha256"], merged["last_synced_remote_version"],
            merged["status"], merged["last_sync_at"], merged["last_error"],
        ),
    )


def _remote_values(item: dict) -> dict:
    parents = item.get("parents", []) if isinstance(item.get("parents", []), list) else []
    return {
        "remote_name": str(item.get("name", "")),
        "mime_type": str(item.get("mimeType", "application/octet-stream")),
        "remote_parent_id": str(parents[0]) if parents else "",
        "remote_md5": str(item.get("md5Checksum", "")),
        "remote_modified_at": str(item.get("modifiedTime", "")),
        "remote_version": str(item.get("version", "")),
        "web_view_link": str(item.get("webViewLink", "")),
    }


def _write_remote_file(
    store: DocumentStore,
    relative_path: str,
    content: bytes,
    actor: str,
    max_bytes: int,
) -> dict:
    target = store.root / Path(*PurePosixPath(relative_path).parts)
    _ensure_local_folder(store, PurePosixPath(relative_path).parent.as_posix())
    if target.exists():
        try:
            document = store.get_document(relative_path)
        except ValueError:
            store._scan_file(target, force_hash=True)
            document = store.get_document(relative_path)
        expected = sha256_file(target)
        return store.replace_content(
            document["document_id"], content, actor,
            expected_sha256=expected, source="google-drive", max_bytes=max_bytes,
        )
    return store.create_document_at(relative_path, content, actor, max_bytes=max_bytes)


def _process_remote_item(
    client: DriveClient,
    store: DocumentStore,
    db,
    user_id: int,
    root_id: str,
    item: dict,
    actor: str,
    direction: str,
    max_bytes: int,
    stats: dict[str, int],
    *,
    forced_relative: str = "",
) -> None:
    drive_id = str(item.get("id", "")).strip()
    if not drive_id:
        return
    existing = _mapping_by_drive(db, user_id, drive_id)
    mime_type = str(item.get("mimeType", "application/octet-stream"))
    values = _remote_values(item)
    remote_version = values["remote_version"]
    local_relative = str(existing["local_relative_path"] if existing else "") or forced_relative
    if not local_relative:
        parent_local = _mapping_parent_path(db, user_id, root_id, item.get("parents", []))
        if parent_local:
            local_relative = f"{parent_local}/{safe_drive_name(item.get('name', ''), drive_id)}"
    if not existing and local_relative and (store.root / Path(*PurePosixPath(local_relative).parts)).exists():
        local_relative = _unique_relative(store, local_relative, drive_id)

    if mime_type == FOLDER_MIME:
        if not local_relative:
            stats["skipped"] += 1
            return
        _ensure_local_folder(store, local_relative)
        _upsert_link(db, user_id, drive_id, {
            **values,
            "local_relative_path": local_relative,
            "last_synced_remote_version": remote_version,
            "status": "synced",
            "last_error": "",
        })
        stats["folders"] += 1
        return

    if mime_type.startswith(GOOGLE_NATIVE_PREFIX):
        _upsert_link(db, user_id, drive_id, {
            **values,
            "local_relative_path": "",
            "last_synced_remote_version": remote_version,
            "status": "cloud_only",
            "last_error": "",
        })
        stats["cloud_only"] += 1
        return

    if not local_relative:
        stats["skipped"] += 1
        return
    target = store.root / Path(*PurePosixPath(local_relative).parts)
    local_sha = sha256_file(target) if target.is_file() else ""
    last_local = str(existing["last_synced_local_sha256"] if existing else "")
    last_remote = str(existing["last_synced_remote_version"] if existing else "")
    decision = sync_decision(local_sha, last_local, remote_version, last_remote)
    if existing and decision == "conflict":
        _upsert_link(db, user_id, drive_id, {
            **values,
            "local_relative_path": local_relative,
            "local_sha256": local_sha,
            "last_synced_local_sha256": last_local,
            "last_synced_remote_version": last_remote,
            "status": "conflict",
            "last_error": "local and Google Drive changed since last sync",
        })
        stats["conflicts"] += 1
        return
    needs_download = not existing or not target.is_file() or decision == "download"
    if not needs_download:
        _upsert_link(db, user_id, drive_id, {**values, "local_relative_path": local_relative, "local_sha256": local_sha})
        return
    if direction not in {"bidirectional", "download_only"}:
        _upsert_link(db, user_id, drive_id, {
            **values,
            "local_relative_path": local_relative,
            "local_sha256": local_sha,
            "status": "remote_changed",
        })
        stats["skipped"] += 1
        return
    content = client.download_file(drive_id, max_bytes)
    document = _write_remote_file(store, local_relative, content, actor, max_bytes)
    current_sha = str(document.get("sha256", ""))
    _upsert_link(db, user_id, drive_id, {
        **values,
        "document_id": str(document.get("document_id", "")),
        "local_relative_path": local_relative,
        "local_sha256": current_sha,
        "last_synced_local_sha256": current_sha,
        "last_synced_remote_version": remote_version,
        "status": "synced",
        "last_error": "",
    })
    stats["downloaded"] += 1


def _sync_remote_tree(
    client: DriveClient,
    store: DocumentStore,
    db,
    user_id: int,
    root_id: str,
    parent_id: str,
    parent_local: str,
    actor: str,
    direction: str,
    max_bytes: int,
    stats: dict[str, int],
    *,
    depth: int = 0,
) -> None:
    if depth > 20:
        raise RuntimeError("Google Drive folder nesting exceeds 20 levels")
    for item in client.list_children(parent_id):
        stats["remote_seen"] += 1
        if stats["remote_seen"] > 10_000:
            raise RuntimeError("Google Drive sync is limited to 10000 managed items")
        drive_id = str(item.get("id", "")).strip()
        relative = f"{parent_local}/{safe_drive_name(item.get('name', ''), drive_id)}"
        _process_remote_item(
            client, store, db, user_id, root_id, item, actor, direction, max_bytes, stats,
            forced_relative=relative,
        )
        if item.get("mimeType") == FOLDER_MIME and drive_id:
            row = _mapping_by_drive(db, user_id, drive_id)
            child_local = str(row["local_relative_path"] if row else relative)
            _sync_remote_tree(
                client, store, db, user_id, root_id, drive_id, child_local,
                actor, direction, max_bytes, stats, depth=depth + 1,
            )


def _ensure_remote_parent(client: DriveClient, db, user_id: int, root_id: str, relative_parent: str) -> str:
    path = PurePosixPath(relative_parent)
    parts = list(path.parts)
    if not parts or parts[0] != LOCAL_SYNC_FOLDER:
        raise ValueError("local Google Drive path is outside the sync folder")
    parent_id = root_id
    current = LOCAL_SYNC_FOLDER
    for part in parts[1:]:
        current = f"{current}/{part}"
        row = _mapping_by_local(db, user_id, current)
        if row and row["mime_type"] == FOLDER_MIME:
            parent_id = str(row["drive_file_id"])
            continue
        created = client.create_folder(part, parent_id)
        drive_id = str(created.get("id", "")).strip()
        if not drive_id:
            raise RuntimeError("Google Drive did not return a folder ID")
        _upsert_link(db, user_id, drive_id, {
            **_remote_values(created),
            "local_relative_path": current,
            "last_synced_remote_version": str(created.get("version", "")),
            "status": "synced",
            "last_error": "",
        })
        parent_id = drive_id
    return parent_id


def _sync_local_files(
    client: DriveClient,
    store: DocumentStore,
    db,
    user_id: int,
    root_id: str,
    actor: str,
    direction: str,
    max_bytes: int,
    stats: dict[str, int],
) -> None:
    if direction not in {"bidirectional", "upload_only"}:
        return
    sync_root = store.root / LOCAL_SYNC_FOLDER
    for path in sorted(sync_root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        if path.name == POLICY_FILE or any(part in {CONTROL_DIR, HISTORY_DIR, PREVIEW_CACHE_DIR} for part in path.parts):
            continue
        relative = path.relative_to(store.root).as_posix()
        mapping = _mapping_by_local(db, user_id, relative)
        if mapping and mapping["mime_type"].startswith(GOOGLE_NATIVE_PREFIX):
            continue
        size = path.stat().st_size
        if size > max_bytes:
            stats["skipped"] += 1
            if mapping:
                _upsert_link(db, user_id, str(mapping["drive_file_id"]), {
                    "local_sha256": sha256_file(path),
                    "status": "too_large",
                    "last_error": f"file exceeds {max_bytes} bytes",
                })
            continue
        local_sha = sha256_file(path)
        if mapping:
            last_local = str(mapping["last_synced_local_sha256"] or "")
            remote_version = str(mapping["remote_version"] or "")
            last_remote = str(mapping["last_synced_remote_version"] or "")
            decision = sync_decision(local_sha, last_local, remote_version, last_remote)
            if decision == "conflict":
                _upsert_link(db, user_id, str(mapping["drive_file_id"]), {
                    "local_sha256": local_sha,
                    "status": "conflict",
                    "last_error": "local and Google Drive changed since last sync",
                })
                stats["conflicts"] += 1
                continue
            if decision != "upload" and last_local:
                continue
        parent_id = _ensure_remote_parent(client, db, user_id, root_id, str(PurePosixPath(relative).parent))
        content = path.read_bytes()
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if mapping:
            item = client.update_file(str(mapping["drive_file_id"]), path.name, content, mime_type)
        else:
            item = client.create_file(parent_id, path.name, content, mime_type)
        drive_id = str(item.get("id", "")).strip()
        if not drive_id:
            raise RuntimeError("Google Drive did not return a file ID")
        try:
            document = store.get_document(relative)
        except ValueError:
            store._scan_file(path, force_hash=True)
            document = store.get_document(relative)
        remote_version = str(item.get("version", ""))
        _upsert_link(db, user_id, drive_id, {
            **_remote_values(item),
            "document_id": str(document.get("document_id", "")),
            "local_relative_path": relative,
            "local_sha256": local_sha,
            "last_synced_local_sha256": local_sha,
            "last_synced_remote_version": remote_version,
            "status": "synced",
            "last_error": "",
        })
        stats["uploaded"] += 1


def _process_changes(
    client: DriveClient,
    store: DocumentStore,
    db,
    user_id: int,
    root_id: str,
    page_token: str,
    actor: str,
    direction: str,
    max_bytes: int,
    stats: dict[str, int],
) -> str:
    changes, new_token = client.list_changes(page_token)
    changes.sort(key=lambda change: 0 if change.get("file", {}).get("mimeType") == FOLDER_MIME else 1)
    for change in changes:
        drive_id = str(change.get("fileId", "")).strip()
        existing = _mapping_by_drive(db, user_id, drive_id) if drive_id else None
        if change.get("removed") or not isinstance(change.get("file"), dict):
            if existing:
                _upsert_link(db, user_id, drive_id, {
                    "status": "remote_deleted",
                    "last_error": "Google Drive item was removed; local data was kept",
                })
                stats["remote_deleted"] += 1
            continue
        item = change["file"]
        parents = item.get("parents", []) if isinstance(item.get("parents", []), list) else []
        relevant = bool(existing) or root_id in parents or bool(_mapping_parent_path(db, user_id, root_id, parents))
        if not relevant:
            continue
        _process_remote_item(client, store, db, user_id, root_id, item, actor, direction, max_bytes, stats)
    return new_token


def sync_google_drive(user_id: int, actor: str) -> dict[str, int | str]:
    """Synchronize the current user's app-managed Drive tree without hard deletes."""
    state = ensure_drive_state(user_id)
    direction = str(state.get("direction", "bidirectional"))
    if not state.get("enabled") or direction == "none":
        return {"status": "disabled", "uploaded": 0, "downloaded": 0, "conflicts": 0}
    access_token = google_access_token(user_id, required_scope=GOOGLE_DRIVE_SCOPE)
    client = DriveClient(access_token)
    db = get_db()
    max_bytes = drive_max_bytes()
    store = DocumentStore(current_app.config["DOCUMENT_ROOT"])
    store.initialize()
    _ensure_local_folder(store, LOCAL_SYNC_FOLDER)
    stats: dict[str, int] = {
        "uploaded": 0, "downloaded": 0, "cloud_only": 0, "conflicts": 0,
        "folders": 0, "skipped": 0, "remote_deleted": 0, "remote_seen": 0,
    }
    try:
        root_id = str(state.get("root_folder_id", "")).strip()
        if not root_id:
            root = client.find_managed_root() or client.create_folder(DRIVE_ROOT_NAME, root_marker=True)
            root_id = str(root.get("id", "")).strip()
            if not root_id:
                raise RuntimeError("Google Drive did not return the managed root folder ID")
            db.execute(
                """UPDATE google_drive_state SET root_folder_id=?, root_folder_name=?, updated_at=CURRENT_TIMESTAMP
                   WHERE user_id=?""",
                (root_id, DRIVE_ROOT_NAME, int(user_id)),
            )
            db.commit()
        page_token = str(state.get("page_token", "")).strip()
        if page_token:
            new_token = _process_changes(
                client, store, db, user_id, root_id, page_token,
                actor, direction, max_bytes, stats,
            )
        else:
            new_token = client.get_start_page_token()
            _sync_remote_tree(
                client, store, db, user_id, root_id, root_id, LOCAL_SYNC_FOLDER,
                actor, direction, max_bytes, stats,
            )
        _sync_local_files(client, store, db, user_id, root_id, actor, direction, max_bytes, stats)
        db.execute(
            """UPDATE google_drive_state
               SET page_token=?, last_sync_at=?, last_error='', updated_at=CURRENT_TIMESTAMP
               WHERE user_id=?""",
            (new_token, utc_now(), int(user_id)),
        )
        db.commit()
        return {"status": "ok", **stats}
    except Exception as exc:
        db.rollback()
        db.execute(
            """UPDATE google_drive_state SET last_error=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?""",
            (f"{type(exc).__name__}: {str(exc)[:500]}", int(user_id)),
        )
        db.commit()
        raise
