"""Persistent capture inbox: capture first, classify later (issue #767).

Same storage architecture as the inventory sidecar (``CONTROL_DIR`` JSON
index + media tree, atomic writes, file lock, sha256, uuid filenames) —
no second media backend. Entries are actor-scoped inside the tenant
root: only the capturing user sees their own inbox. Originals are kept
byte-identical (with EXIF); access is login + actor checked, previews
are served with ``no-store`` and never leave the machine.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock

ENTRY_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
FILENAME_RE = re.compile(r"[0-9a-f]{32}\.(?:jpg|png|webp)\Z")
REQUEST_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
BATCH_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
IMAGE_EXTENSIONS = (".jpg", ".png", ".webp")
STATUS_UNASSIGNED = "unassigned"
STATUS_ASSIGNED = "assigned"
ASSIGN_KINDS = ("book", "object")
MAX_BATCH_PHOTOS = 12


class CaptureInboxIndexError(Exception):
    """The inbox index is unreadable or corrupt; fail closed, never reset."""


class CaptureInboxConflict(ValueError):
    """Same actor + token was reused with different image bytes."""


class CaptureInboxStore:
    """Actor-scoped persistent inbox for unclassified captures."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.directory = self.root / CONTROL_DIR / "inventory"
        self.media_directory = self.directory / "media" / "inbox"
        self.index_path = self.directory / "capture-inbox.json"
        self.lock_path = self.directory / ".capture-inbox-write.lock"

    def _read(self) -> dict[str, Any]:
        try:
            text = self.index_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise CaptureInboxIndexError(
                "Eingangskorb-Index ist nicht lesbar."
            ) from exc
        try:
            value = json.loads(text)
        except ValueError as exc:
            raise CaptureInboxIndexError(
                "Eingangskorb-Index ist beschädigt."
            ) from exc
        if not isinstance(value, dict):
            raise CaptureInboxIndexError(
                "Eingangskorb-Index hat ein ungültiges Format."
            )
        return value

    def _entries(self, data: dict[str, Any]) -> dict[str, Any]:
        entries = data.get("entries", {})
        if not isinstance(entries, dict):
            raise CaptureInboxIndexError(
                "Eingangskorb-Index hat ein ungültiges Format."
            )
        return entries

    def create(
        self,
        actor: str,
        data: bytes,
        extension: str,
        client_request_id: str = "",
        batch_id: str = "",
    ) -> tuple[dict[str, Any], bool]:
        """Store one original photo; idempotent per actor.

        Returns (entry, duplicate). The same actor + client request id
        + same sha256 returns the existing entry (idempotent replay,
        unchanged even when another batch was requested — no silent
        regrouping); the same actor + same sha256 with a different
        token returns the existing entry only within the same batch
        (deliberate content dedup never mixes rapid successive books).
        The same actor + token with a different sha256 raises
        :class:`CaptureInboxConflict` without any write.
        """
        clean_actor = str(actor or "").strip()
        if not clean_actor:
            raise ValueError("user is required")
        if not data:
            raise ValueError("photo is required")
        if extension not in IMAGE_EXTENSIONS:
            raise ValueError("Nur JPEG, PNG und WebP werden unterstützt")
        request_id = str(client_request_id or "").strip()
        if request_id and not REQUEST_ID_RE.fullmatch(request_id):
            raise ValueError("Erfassungskennung ist ungültig")
        clean_batch = str(batch_id or "").strip().lower()
        if clean_batch and not BATCH_ID_RE.fullmatch(clean_batch):
            raise ValueError("Buchgruppe ist ungültig")
        digest = hashlib.sha256(data).hexdigest()
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            index = self._read()
            entries = self._entries(index)
            for existing in entries.values():
                if not isinstance(existing, dict):
                    continue
                if existing.get("actor") != clean_actor:
                    continue
                if request_id and existing.get("client_request_id") == request_id:
                    original = existing.get("original", {})
                    stored = original.get("sha256") if isinstance(original, dict) else None
                    if stored == digest:
                        return dict(existing), True
                    raise CaptureInboxConflict(
                        "Erfassungskennung wurde bereits mit einem anderen "
                        "Foto verwendet. Bitte neues Foto mit neuer Kennung senden."
                    )
            for existing in entries.values():
                if not isinstance(existing, dict):
                    continue
                if existing.get("actor") != clean_actor:
                    continue
                if str(existing.get("batch_id", "") or "") != clean_batch:
                    continue
                original = existing.get("original", {})
                if isinstance(original, dict) and original.get("sha256") == digest:
                    return dict(existing), True
            if clean_batch:
                grouped = sum(
                    1 for item in entries.values()
                    if isinstance(item, dict)
                    and item.get("actor") == clean_actor
                    and str(item.get("batch_id", "") or "") == clean_batch
                )
                if grouped >= MAX_BATCH_PHOTOS:
                    raise ValueError(
                        "Buchgruppe ist voll "
                        f"(maximal {MAX_BATCH_PHOTOS} Fotos)."
                    )
            entry_id = uuid.uuid4().hex
            filename = f"{uuid.uuid4().hex}{extension}"
            target_dir = self.media_directory / entry_id
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / filename
            target.write_bytes(data)
            now = utc_now()
            entry = {
                "entry_id": entry_id,
                "actor": clean_actor,
                "client_request_id": request_id,
                "batch_id": clean_batch,
                "status": STATUS_UNASSIGNED,
                "created_at": now,
                "updated_at": now,
                "original": {
                    "filename": filename,
                    "sha256": digest,
                    "size": len(data),
                },
                "analysis": {},
                "assignment": {},
                "history": [{
                    "event": "captured",
                    "created_at": now,
                    "created_by": clean_actor,
                }],
            }
            try:
                entries[entry_id] = entry
                index["entries"] = entries
                index["version"] = 1
                atomic_json_write(self.index_path, index)
            except Exception:
                try:
                    target.unlink(missing_ok=True)
                finally:
                    raise
            return dict(entry), False

    def get(self, entry_id: str, actor: str) -> dict[str, Any] | None:
        """Return one entry owned by actor, else None (no leakage)."""
        if not ENTRY_ID_RE.fullmatch(str(entry_id or "")):
            return None
        entries = self._entries(self._read())
        entry = entries.get(str(entry_id))
        if not isinstance(entry, dict) or entry.get("actor") != str(actor or ""):
            return None
        return dict(entry)

    def list(self, actor: str, status: str = "") -> list[dict[str, Any]]:
        """Newest-first inbox entries owned by actor, optional status filter."""
        clean_actor = str(actor or "")
        rows = [
            dict(entry)
            for entry in self._entries(self._read()).values()
            if isinstance(entry, dict) and entry.get("actor") == clean_actor
            and (not status or entry.get("status") == status)
        ]
        rows.sort(key=lambda row: str(row.get("created_at", "")), reverse=True)
        return rows

    def list_batch(self, actor: str, batch_id: str) -> list[dict[str, Any]]:
        """Oldest-first entries of one book group owned by actor.

        Capture order is preserved (title page, imprint, back cover).
        Raises ValueError for a malformed batch id; an unknown or
        foreign batch simply yields no rows (no leakage).
        """
        clean_batch = str(batch_id or "").strip().lower()
        if not BATCH_ID_RE.fullmatch(clean_batch):
            raise ValueError("Buchgruppe ist ungültig")
        clean_actor = str(actor or "")
        rows = [
            dict(entry)
            for entry in self._entries(self._read()).values()
            if isinstance(entry, dict) and entry.get("actor") == clean_actor
            and str(entry.get("batch_id", "") or "") == clean_batch
        ]
        rows.sort(key=lambda row: (
            str(row.get("created_at", "")), str(row.get("entry_id", ""))))
        return rows

    def set_batch(
        self, entry_id: str, actor: str, batch_id: str
    ) -> dict[str, Any] | None:
        """Move one owned entry into another book group (or ungroup).

        An empty batch id removes the entry from its group. The
        original, analysis, assignment and history are preserved; only
        a ``regrouped`` history event is appended. Repeating the same
        batch is idempotent (no new history). Raises ValueError for a
        malformed batch id or a full target group.
        """
        clean_batch = str(batch_id or "").strip().lower()
        if clean_batch and not BATCH_ID_RE.fullmatch(clean_batch):
            raise ValueError("Buchgruppe ist ungültig")
        if not ENTRY_ID_RE.fullmatch(str(entry_id or "")):
            return None
        with exclusive_file_lock(self.lock_path):
            index = self._read()
            entries = self._entries(index)
            entry = entries.get(str(entry_id))
            if not isinstance(entry, dict) or entry.get("actor") != str(actor or ""):
                return None
            previous = str(entry.get("batch_id", "") or "")
            if previous == clean_batch:
                return dict(entry)
            if clean_batch:
                grouped = sum(
                    1 for item in entries.values()
                    if isinstance(item, dict)
                    and item.get("actor") == str(actor or "")
                    and str(item.get("batch_id", "") or "") == clean_batch
                )
                if grouped >= MAX_BATCH_PHOTOS:
                    raise ValueError(
                        "Buchgruppe ist voll "
                        f"(maximal {MAX_BATCH_PHOTOS} Fotos)."
                    )
            now = utc_now()
            entry["batch_id"] = clean_batch
            entry["updated_at"] = now
            history = entry.setdefault("history", [])
            if isinstance(history, list):
                history.append({
                    "event": "regrouped",
                    "previous_batch_id": previous,
                    "batch_id": clean_batch,
                    "created_at": now,
                    "created_by": str(actor),
                })
            index["entries"] = entries
            index["version"] = 1
            atomic_json_write(self.index_path, index)
            return dict(entry)

    def save_analysis(
        self, entry_id: str, actor: str, analysis: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Store the latest analysis; unchanged content adds no history."""
        if not ENTRY_ID_RE.fullmatch(str(entry_id or "")):
            return None
        with exclusive_file_lock(self.lock_path):
            index = self._read()
            entries = self._entries(index)
            entry = entries.get(str(entry_id))
            if not isinstance(entry, dict) or entry.get("actor") != str(actor or ""):
                return None
            try:
                fingerprint = json.dumps(analysis, sort_keys=True, default=str)
            except (TypeError, ValueError):
                fingerprint = ""
            previous = json.dumps(entry.get("analysis", {}), sort_keys=True, default=str)
            now = utc_now()
            entry["analysis"] = dict(analysis)
            entry["updated_at"] = now
            if fingerprint != previous:
                history = entry.setdefault("history", [])
                if isinstance(history, list):
                    history.append({
                        "event": "analyzed",
                        "created_at": now,
                        "created_by": str(actor),
                    })
            index["entries"] = entries
            index["version"] = 1
            atomic_json_write(self.index_path, index)
            return dict(entry)

    def assign(
        self,
        entry_id: str,
        actor: str,
        kind: str,
        object_id: str,
        display_id: str = "",
        name: str = "",
    ) -> dict[str, Any] | None:
        """Link an entry to a book/object; repeat assignment is idempotent."""
        clean_kind = str(kind or "").strip().lower()
        clean_object = str(object_id or "").strip()
        if clean_kind not in ASSIGN_KINDS or not clean_object:
            raise ValueError("assignment kind and object are required")
        if not ENTRY_ID_RE.fullmatch(str(entry_id or "")):
            return None
        with exclusive_file_lock(self.lock_path):
            index = self._read()
            entries = self._entries(index)
            entry = entries.get(str(entry_id))
            if not isinstance(entry, dict) or entry.get("actor") != str(actor or ""):
                return None
            current = entry.get("assignment", {})
            if not isinstance(current, dict):
                current = {}
            if (current.get("kind") == clean_kind
                    and current.get("object_id") == clean_object):
                return dict(entry)
            now = utc_now()
            entry["assignment"] = {
                "kind": clean_kind,
                "object_id": clean_object,
                "display_id": str(display_id or ""),
                "name": str(name or ""),
                "assigned_at": now,
                "assigned_by": str(actor),
            }
            entry["status"] = STATUS_ASSIGNED
            entry["updated_at"] = now
            history = entry.setdefault("history", [])
            if isinstance(history, list):
                event: dict[str, Any] = {
                    "event": "assigned",
                    "kind": clean_kind,
                    "object_id": clean_object,
                    "created_at": now,
                    "created_by": str(actor),
                }
                if current.get("object_id"):
                    event["previous"] = {
                        "kind": str(current.get("kind", "")),
                        "object_id": str(current.get("object_id", "")),
                    }
                history.append(event)
            index["entries"] = entries
            index["version"] = 1
            atomic_json_write(self.index_path, index)
            return dict(entry)

    def media_path(self, entry_id: str, actor: str) -> Path:
        """Validated original path; raises ValueError when not available."""
        entry = self.get(entry_id, actor)
        original = (entry or {}).get("original", {})
        filename = str(original.get("filename", "")) if isinstance(original, dict) else ""
        if not FILENAME_RE.fullmatch(filename):
            raise ValueError("Original ist nicht verfügbar")
        path = (self.media_directory / str(entry_id) / filename).resolve()
        try:
            base = self.media_directory.resolve()
        except OSError:
            raise ValueError("Original ist nicht verfügbar") from None
        if base not in path.parents or not path.is_file() or path.is_symlink():
            raise ValueError("Original ist nicht verfügbar")
        return path
