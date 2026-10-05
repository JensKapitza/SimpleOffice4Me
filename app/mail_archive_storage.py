"""Verified archive reads using the existing storage authority and namespace."""
from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

from .document_store import DocumentStore
from .mail_client import MAX_MESSAGE_BYTES, MailStore, _owner_key
from .safe_paths import relative_under
from .v2.catalog import ObjectCatalog
from .v2.contracts import LogicalObjectId
from .v2.cutover import load_cutover_state
from .v2.storage_runtime import storage_for

ARCHIVE_ID = re.compile(r"^[0-9a-f]{128}$")


def archive_locations(store: MailStore, actor: str, account_id: str) -> list[str]:
    """Use the V2 namespace when authoritative; retain unmanaged V1 archives."""
    store._owned_row(actor, account_id)
    prefix = f"email/{_owner_key(actor)}/{account_id}/"
    if load_cutover_state(store.root).mode == "v2":
        entries = [entry for entry in ObjectCatalog(store.root).list()
                   if entry.location.relative_path.startswith(prefix)
                   and entry.location.relative_path.endswith(".eml")]
        return [entry.location.relative_path for entry in
                sorted(entries, key=lambda entry: (entry.updated_at, entry.location.relative_path), reverse=True)]
    base = store.root / prefix
    if not base.is_dir():
        return []
    paths = [path for path in base.rglob("*.eml") if path.is_file() and not path.is_symlink()]
    return [path.relative_to(store.root).as_posix() for path in
            sorted(paths, key=lambda path: (path.stat().st_mtime, str(path)), reverse=True)]


def archive_document(store: MailStore, actor: str, account_id: str, path: str) -> dict:
    store._owned_row(actor, account_id)
    relative = relative_under(store.root, path, require_name=True)
    if relative.suffix.casefold() != ".eml":
        raise ValueError("invalid archive path")
    if relative.parts[:3] != ("email", _owner_key(actor), account_id):
        raise PermissionError("archive path is outside the owned mail archive")
    documents = DocumentStore(store.root)
    if load_cutover_state(store.root).mode == "v2":
        entry = ObjectCatalog(store.root).get_by_location(relative.as_posix())
        if not entry.ok:
            raise FileNotFoundError("archive message does not exist")
        document = documents.get_document(entry.value.object_id.value)
    else:
        if not (store.root / relative).is_file():
            raise FileNotFoundError("archive message does not exist")
        document = documents.get_document(relative.as_posix())
    if document.get("deleted_at") or document.get("system_state") == "webdav_deleted":
        raise FileNotFoundError("archive message is deleted")
    return document


def read_archive_eml(store: MailStore, actor: str, account_id: str, path: str) -> bytes:
    document = archive_document(store, actor, account_id, path)
    relative = relative_under(store.root, path, require_name=True)
    try:
        with io.BytesIO() as target:
            result = storage_for(store.root, actor).copy_verified_range_to(
                LogicalObjectId(document["document_id"]), target,
                start=0, length=MAX_MESSAGE_BYTES + 1,
            )
            if not result.ok:
                raise ValueError("archive content is unavailable or failed verification")
            raw = target.getvalue()
        if result.value.size != len(raw) or len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError("message exceeds 100 MiB preview limit")
        if result.value.location.relative_path != relative.as_posix():
            raise ValueError("archive namespace changed during read")
        if hashlib.sha256(raw).hexdigest() != result.value.version:
            raise ValueError("archive content failed verification")
        if ARCHIVE_ID.fullmatch(relative.stem) and hashlib.sha512(raw).hexdigest() != relative.stem:
            raise ValueError("archive message does not match its SHA-512 identity")
        return raw
    except (OSError, RuntimeError) as exc:
        raise ValueError("archive storage is unavailable") from exc


def archive_path_by_id(store: MailStore, actor: str, account_id: str, archive_id: str) -> str:
    archive_id = archive_id.strip().casefold()
    if not ARCHIVE_ID.fullmatch(archive_id):
        raise ValueError("invalid archive message id")
    matches = [path for path in archive_locations(store, actor, account_id)
               if Path(path).name == f"{archive_id}.eml"]
    if len(matches) != 1:
        raise FileNotFoundError("archive message does not exist or is ambiguous")
    return matches[0]
