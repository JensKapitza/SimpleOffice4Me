"""Store chat attachments as managed SimpleOffice documents with optional room ACLs."""
from __future__ import annotations

import hashlib
import io
import mimetypes
import re
from pathlib import Path
from typing import Any

from .chat_access import CHAT_ATTRIBUTE
from .document_store import DocumentStore, atomic_json_write
from .safe_paths import resolve_file_under


_ROOM_RE = re.compile(r"^[0-9a-f-]{36}$")


def _safe_filename(filename: str) -> str:
    value = Path(str(filename or "datei")).name.replace("/", "_").replace("\\", "_")
    value = "".join(ch for ch in value if ord(ch) >= 32 and ch != "\x7f").strip()
    return value[:220] or "datei"


def _configure_private_folder(store: DocumentStore, room_id: str, local_users: list[str], admin_users: list[str]) -> Path:
    if not _ROOM_RE.fullmatch(room_id):
        raise ValueError("Ungültige Chat-ID")
    folder = (store.root / "Chat" / room_id).resolve()
    folder.relative_to(store.root)
    folder.mkdir(parents=True, exist_ok=True)
    policy_path = store.ensure_folder_policy(folder)
    policy = store._read_json(policy_path, {})
    grants: dict[str, str] = {}
    for username in local_users:
        if str(username).strip():
            grants[str(username).strip()] = "read"
    for username in admin_users:
        if str(username).strip():
            grants[str(username).strip()] = "manage"
    policy["access_enabled"] = True
    policy["inherit"] = False
    policy["grants"] = [{"principal": username, "role": role} for username, role in sorted(grants.items(), key=lambda item: item[0].casefold())]
    atomic_json_write(policy_path, policy)
    return folder


def save_attachment(root: str | Path, content: bytes, filename: str, actor: str, *, room_id: str, attachment_id: str, local_users: list[str], admin_users: list[str], visibility: str, source_peer: str = "", max_bytes: int = 512 * 1024 * 1024) -> dict[str, Any]:
    if len(content) > max_bytes:
        raise ValueError("Anhang überschreitet das Upload-Limit")
    safe_name = _safe_filename(filename)
    store = DocumentStore(root)
    if visibility == "chat":
        folder = _configure_private_folder(store, room_id, local_users, admin_users)
        relative = (folder / f"{attachment_id[:8]}-{safe_name}").relative_to(store.root).as_posix()
        document = store.create_document_at(relative, content, actor, max_bytes=max_bytes)
    elif visibility == "documents":
        document = store.import_upload(io.BytesIO(content), safe_name, actor, archive=True, max_bytes=max_bytes)
    else:
        raise ValueError("Ungültige Anhang-Sichtbarkeit")
    digest = hashlib.sha256(content).hexdigest()
    if str(document.get("sha256") or "").casefold() != digest:
        raise RuntimeError("Gespeicherter Chat-Anhang hat eine abweichende Prüfsumme")
    metadata = store.get_document(document["document_id"])
    metadata.setdefault("attributes", {})[CHAT_ATTRIBUTE] = {"schema": 1, "room_id": room_id, "attachment_id": attachment_id, "visibility": visibility, "allowed_local_users": sorted(set(local_users)), "origin_peer": str(source_peer or ""), "original_filename": safe_name}
    metadata["tags"] = sorted(set([*metadata.get("tags", []), "chat-attachment"]))
    store._save_document(metadata)
    store._refresh_search_index(metadata)
    try:
        path = resolve_file_under(store.root, str(metadata.get("last_path", "")))
        store._write_xattrs(path, metadata["document_id"], metadata.get("sha256", ""), metadata.get("tags", []))
    except (OSError, ValueError):
        pass
    return metadata


def attachment_bytes(root: str | Path, document_id: str) -> bytes:
    store = DocumentStore(root)
    document = store.get_document(document_id)
    path = resolve_file_under(store.root, str(document.get("last_path", "")))
    return path.read_bytes()


def guessed_mime(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"
