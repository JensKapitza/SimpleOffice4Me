"""Virtual S3 object projection for documents and safe metadata."""
from __future__ import annotations

import hashlib
import json
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, BinaryIO

from flask import current_app

from app.document_store import DocumentStore
from app.contact_store import ContactStore
from app.calendar_store import CalendarStore
from app.todo_store import TodoStore
from app.safe_paths import resolve_file_under
from app.virtual_filesystem import VirtualFileSystem
from app.v2.contracts import LogicalObjectId, StorageLocation
from app.v2.storage_runtime import storage_for


@dataclass(frozen=True)
class S3Object:
    key: str
    size: int
    etag: str
    modified: datetime
    content_type: str
    body: bytes | None = None
    document_id: str = ""
    source_path: str = ""


def _timestamp(value: object) -> datetime:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        result = datetime.now(timezone.utc)
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def _safe_metadata(document: dict[str, Any]) -> bytes:
    fields = ("document_id", "last_path", "size", "sha256", "tags", "state", "first_seen_at",
              "last_seen_at", "version_series_id", "version_number", "content_sha256")
    safe = {key: document[key] for key in fields if key in document}
    return json.dumps({"schema": "simpleoffice-document-v1", **safe}, ensure_ascii=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


class DocumentObjects:
    """Read enabled domain projections through their existing authorization paths."""

    def __init__(self, username: str, *, documents_enabled: bool = True, contacts_enabled: bool = True,
                 calendar_enabled: bool = True):
        self.username = username
        self.documents_enabled = documents_enabled
        self.contacts_enabled = contacts_enabled
        self.calendar_enabled = calendar_enabled
        self.root = current_app.config["DOCUMENT_ROOT"]
        self.store = DocumentStore(self.root)
        self.contacts = ContactStore(self.root)
        self.calendar = CalendarStore(self.root)
        self.todos = TodoStore(self.root)
        self.vfs = VirtualFileSystem.from_environment(self.root)
        self.actor = f"s3:{username}"

    def _authorized(self, document: dict[str, Any]) -> bool:
        if not self.documents_enabled:
            return False
        path = str(document.get("last_path") or "")
        if not path or path.startswith("[external]") or document.get("deleted_at"):
            return False
        try:
            resource = self.vfs.resolve(path, allow_missing=False)
            return self.vfs.allows(self.actor, resource, "read")
        except (OSError, ValueError, PermissionError):
            return False

    def _document(self, document_id: str) -> dict[str, Any] | None:
        try:
            row = self.store.get_document(document_id)
        except (OSError, ValueError):
            return None
        return row if self._authorized(row) else None

    def resolve(self, key: str) -> S3Object | None:
        if key == "_meta/overlay.json":
            prefixes = ["_meta/"]
            if self.contacts_enabled:
                prefixes.append("contacts/")
            if self.documents_enabled:
                prefixes.extend(("documents/", "inbox/", "tasks/"))
            if self.calendar_enabled:
                prefixes.append("calendar/")
            data = json.dumps({"schema": "simpleoffice-s3-overlay-v1", "bucket": "simpleoffice",
                               "prefixes": prefixes,
                               "operations": ["ListBuckets", "HeadBucket", "ListObjects", "ListObjectsV2", "HeadObject", "GetObject", "PutObject(inbox only)"]},
                              sort_keys=True, separators=(",", ":")).encode()
            return S3Object(key, len(data), hashlib.sha256(data).hexdigest(), datetime.now(timezone.utc), "application/json", data)
        parts = key.split("/")
        if len(parts) == 2 and parts[0] == "contacts" and parts[1].endswith(".vcf"):
            if not self.contacts_enabled:
                return None
            contact_id = parts[1][:-4]
            try:
                contact = self.contacts.get(contact_id, self.username)
                payload = self.contacts.vcard(contact_id, self.username).encode("utf-8")
            except (OSError, ValueError):
                return None
            return S3Object(key, len(payload), hashlib.sha256(payload).hexdigest(),
                            _timestamp(contact.get("updated_at")), "text/vcard; charset=utf-8", payload)
        if key == "calendar/events.ics":
            if not self.calendar_enabled:
                return None
            events = self.calendar.events(self.username)
            payload = self.calendar.export_ics(self.username).encode("utf-8")
            modified = max((_timestamp(event.get("updated_at") or event.get("created_at") or event.get("start"))
                            for event in events), default=datetime(1970, 1, 1, tzinfo=timezone.utc))
            return S3Object(key, len(payload), hashlib.sha256(payload).hexdigest(), modified,
                            "text/calendar; charset=utf-8", payload)
        if len(parts) == 2 and parts[0] == "tasks" and parts[1].endswith(".ics"):
            if not self.documents_enabled:
                return None
            item_id = parts[1][:-4]
            task = next((item for item in self.todos.items(self.username) if item.get("id") == item_id), None)
            if task is None:
                return None
            return self._task_object(key, task)
        if len(parts) >= 3 and parts[0] == "inbox" and parts[1] == self.username:
            path = "inbox/" + "/".join(parts[2:])
            try:
                document = self.store.get_document(path)
            except (OSError, ValueError):
                return None
            if not self._authorized(document) or document.get("last_path") != path:
                return None
            size = int(document.get("size") or 0)
            return S3Object(key, size, str(document.get("sha256") or ""), _timestamp(document.get("last_seen_at")),
                            mimetypes.guess_type(path)[0] or "application/octet-stream", None,
                            str(document.get("document_id") or ""), path)
        if len(parts) < 3 or parts[0] != "documents":
            return None
        document_id = parts[1]
        document = self._document(document_id)
        if document is None:
            return None
        modified = _timestamp(document.get("last_seen_at"))
        if parts[2:] == ["metadata.json"]:
            payload = _safe_metadata(document)
            return S3Object(key, len(payload), hashlib.sha256(payload).hexdigest(), modified, "application/json", payload, document_id)
        if len(parts) < 4 or parts[2] != "original":
            return None
        filename = "/".join(parts[3:])
        expected = str(document.get("last_path") or "").replace("\\", "/")
        if filename != expected.rsplit("/", 1)[-1]:
            return None
        try:
            path = self.vfs.resolve(expected, allow_missing=False)
            if not self.vfs.allows(self.actor, path, "read"):
                return None
            size = path.stat().st_size
        except (OSError, ValueError, PermissionError):
            return None
        digest = str(document.get("sha256") or document.get("content_sha256") or "")
        return S3Object(key, size, digest, modified, mimetypes.guess_type(filename)[0] or "application/octet-stream", None, document_id, expected)

    def read(self, obj: S3Object) -> bytes:
        if obj.body is not None:
            return obj.body
        document = self._document(obj.document_id)
        if document is None or str(document.get("last_path") or "") != obj.source_path:
            raise FileNotFoundError("S3 object is unavailable")
        try:
            return self.vfs.read_bytes(self.actor, obj.source_path)
        except (OSError, ValueError, PermissionError) as exc:
            raise FileNotFoundError("S3 object is unavailable") from exc

    @staticmethod
    def _task_object(key: str, task: dict[str, Any]) -> S3Object:
        from app.caldav import _todo_ics
        payload = _todo_ics(task).encode("utf-8")
        modified = _timestamp(task.get("updated_at") or task.get("created_at"))
        return S3Object(key, len(payload), hashlib.sha256(payload).hexdigest(), modified,
                        "text/calendar; charset=utf-8", payload)

    def read_to(self, obj: S3Object, target, *, start: int = 0, length: int | None = None) -> int:
        if obj.body is not None:
            payload = obj.body[start:] if length is None else obj.body[start:start + length]
            return target.write(payload)
        document = self._document(obj.document_id)
        if document is None or str(document.get("last_path") or "") != obj.source_path:
            raise FileNotFoundError("S3 object is unavailable")
        try:
            verified = self.vfs.copy_verified_range_to(
                self.actor, obj.source_path, target, start=start, length=length,
            )
            # StoragePort returns the verified object metadata; compute the
            # copied byte count from the request and its authoritative size.
            if hasattr(verified, "size"):
                return max(0, min(verified.size - start, length if length is not None else verified.size - start))
            return verified
        except (OSError, ValueError, PermissionError) as exc:
            raise FileNotFoundError("S3 object is unavailable") from exc

    def keys(self, *, prefix: str, after: str = "", limit: int = 1000) -> list[S3Object]:
        """Merge enabled calendar, contact, task, and document projections in key order."""
        found: list[S3Object] = []
        meta = self.resolve("_meta/overlay.json")
        if meta and meta.key.startswith(prefix) and meta.key > after:
            found.append(meta)
        calendar_key = "calendar/events.ics"
        if (self.calendar_enabled and calendar_key.startswith(prefix) and calendar_key > after):
            calendar_obj = self.resolve(calendar_key)
            if calendar_obj:
                found.append(calendar_obj)
        if self.documents_enabled and (not prefix or "tasks/".startswith(prefix) or prefix.startswith("tasks/")):
            tasks = sorted(self.todos.items(self.username), key=lambda item: str(item.get("id", "")))
            for task in tasks:
                key = f"tasks/{task['id']}.ics"
                if key.startswith(prefix) and key > after:
                    found.append(self._task_object(key, task))
                    if len(found) >= limit + 1:
                        break
        if self.contacts_enabled and (not prefix or "contacts/".startswith(prefix) or prefix.startswith("contacts/")):
            contact_keys = sorted(
                f"contacts/{contact['contact_id']}.vcf"
                for contact in self.contacts.contacts(self.username)
                if f"contacts/{contact['contact_id']}.vcf".startswith(prefix)
                and f"contacts/{contact['contact_id']}.vcf" > after
            )
            for key in contact_keys[:limit + 1]:
                obj = self.resolve(key)
                if obj:
                    found.append(obj)
        start_id = ""
        marker_parts = after.split("/")
        if len(marker_parts) > 1 and marker_parts[0] == "documents":
            start_id = marker_parts[1]
        last_id = ""
        self.store.initialize()
        while len(found) < limit + 1:
            with self.store._db() as db:
                rows = db.execute(
                    "SELECT document_id FROM document_listing WHERE document_id>=? ORDER BY document_id LIMIT 200",
                    (start_id,),
                ).fetchall()
            if not rows:
                break
            for row in rows:
                document_id = str(row[0])
                if document_id == last_id:
                    continue
                last_id = document_id
                document = self._document(document_id)
                if document is None:
                    continue
                filename = str(document.get("last_path", "")).replace("\\", "/").rsplit("/", 1)[-1]
                candidates = (
                    self.resolve(f"documents/{document_id}/metadata.json"),
                    self.resolve(f"documents/{document_id}/original/{filename}"),
                )
                relative = str(document.get("last_path", "")).replace("\\", "/")
                if relative.startswith(f"inbox/{self.username}/"):
                    candidates += (self.resolve(relative),)
                for obj in candidates:
                    if obj and obj.key.startswith(prefix) and obj.key > after:
                        found.append(obj)
                        if len(found) >= limit + 1:
                            break
                if len(found) >= limit + 1:
                    break
            if len(rows) < 200 or len(found) >= limit + 1:
                break
            start_id = last_id + "\x00"
        return sorted(found, key=lambda item: item.key)[:limit + 1]

    def put_inbox(self, key: str, source: BinaryIO, max_bytes: int, expected_sha256: str) -> dict[str, Any]:
        from app.safe_paths import safe_filename
        from app.file_lock import exclusive_file_lock

        parts = key.split("/")
        if not key or len(key.encode("utf-8")) > 1024 or any(part in {"", ".", ".."} or any(ord(ch) < 32 for ch in part) for part in parts):
            raise ValueError("Invalid inbox object key")
        safe_parts = [safe_filename(part, fallback="upload.bin", max_length=180) for part in parts]
        destination = "inbox/" + self.username + "/" + "/".join(safe_parts)
        lock_dir = self.store.control / "s3-inbox-locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / (hashlib.sha256(destination.encode("utf-8")).hexdigest() + ".lock")
        with exclusive_file_lock(lock_path):
            try:
                existing = self.store.get_document(destination)
            except (OSError, ValueError):
                existing = None
            if existing is not None:
                if str(existing.get("sha256") or "") == expected_sha256:
                    return existing
                raise FileExistsError("Inbox key already exists")
            storage = storage_for(self.root, self.actor)
            result = storage.import_stream_at(StorageLocation(destination), source, max_bytes=max_bytes)
            if not result.ok:
                raise ValueError(result.error.message if result.error else "Inbox import failed")
            object_id = result.value.object_id
            if result.value.version != expected_sha256:
                storage.delete(object_id, expected_version=result.value.version)
                raise ValueError("Uploaded content does not match the declared SHA-256")
            return self.store.get_document(object_id.value)
