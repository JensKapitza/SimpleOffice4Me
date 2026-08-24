"""File based document storage and repairable scan index.

The filesystem is the source of truth.  SQLite only caches scan results and
can therefore be deleted and rebuilt at any time.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import fnmatch
import shutil
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import threading
import uuid
import zipfile
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree

import click
from flask import current_app
from flask.cli import with_appcontext

from .retention import evaluate_deadlines, parse_deadline
from .revision_history import RevisionHistory
from .search_query import compile_query


CONTROL_DIR = ".simpleoffice-meta"
POLICY_FILE = ".simpleoffice-folder.json"
EVENT_FILE = "events.ndjson"
HISTORY_DIR = ".simpleoffice-history"
ARCHIVES_FILE = "archives.json"
ARCHIVE_MARKER = ".simpleoffice-archive.json"
SHARES_FILE = "shares.json"
SSH_SOURCES_FILE = "ssh-sources.json"
MAX_WEBDAV_COLLECTION_MEMBERS = 2_000
MAX_WEBDAV_COLLECTION_DEPTH = 64
COLLECTION_TRASH_DIR = "webdav-collection-trash"
_STORE_INITIALIZATION_LOCK = threading.Lock()
_INITIALIZED_INDEXES: set[Path] = set()
_WAL_CONFIGURATION_LOCK = threading.Lock()
_WAL_CONFIGURED_INDEXES: set[Path] = set()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def ocr_subprocess_environment() -> dict[str, str]:
    """Return a bounded OpenMP environment for one Tesseract process."""
    environment = os.environ.copy()
    requested = environment.get(
        "SIMPLEOFFICE_OCR_THREADS",
        environment.get("OMP_THREAD_LIMIT", "1"),
    ).strip()
    try:
        threads = int(requested)
    except ValueError:
        threads = 1
    environment["OMP_THREAD_LIMIT"] = str(max(1, min(threads, 8)))
    return environment


@dataclass(frozen=True)
class ScanReport:
    files: int = 0
    new_files: int = 0
    duplicates: int = 0
    symlinks: int = 0
    skipped_boundaries: int = 0
    errors: int = 0


class DocumentStore:
    """Filesystem store with xattrs when available and JSON sidecars otherwise."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.documents = self.control / "documents"
        self.fingerprints = self.control / "fingerprints"
        self.events = self.control / EVENT_FILE
        self.index_path = self.control / "index.sqlite3"
        self.archives_path = self.control / ARCHIVES_FILE
        self.shares_path = self.control / SHARES_FILE
        self.ssh_sources_path = self.control / SSH_SOURCES_FILE
        self.scan_status_path = self.control / "scan-status.json"
        self.note_snapshots = self.control / "note-snapshots"
        self.document_access = self.control / "document-access"
        self.history = RevisionHistory(self.root)

    def initialize(self) -> None:
        if self.index_path not in _INITIALIZED_INDEXES or not self.index_path.is_file():
            with _STORE_INITIALIZATION_LOCK:
                if self.index_path not in _INITIALIZED_INDEXES or not self.index_path.is_file():
                    self._initialize_once()
                    _INITIALIZED_INDEXES.add(self.index_path)
        # Recovery is intentionally not cached: an interrupted WebDAV DELETE
        # can leave a new recovery journal at any time during this process.
        self._recover_interrupted_collection_deletions()

    def _initialize_once(self) -> None:
        """Create or migrate the disposable index once per worker process."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.documents.mkdir(parents=True, exist_ok=True)
        self.fingerprints.mkdir(parents=True, exist_ok=True)
        self.document_access.mkdir(parents=True, exist_ok=True)
        self.ensure_folder_policy(self.root)
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS scan_file (
                    relative_path TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    modified_ns INTEGER NOT NULL,
                    device INTEGER,
                    inode INTEGER,
                    last_seen_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS scan_file_sha256 ON scan_file(sha256);
                CREATE TABLE IF NOT EXISTS document_listing (
                    document_id TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    state TEXT NOT NULL,
                    has_notes INTEGER NOT NULL,
                    has_relationships INTEGER NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    version_series_id TEXT NOT NULL DEFAULT '',
                    version_number INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS document_listing_inbox
                    ON document_listing(state, has_notes, has_relationships, last_seen_at DESC);
                CREATE TABLE IF NOT EXISTS document_relationship (
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    propagates_retention INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(source_id, target_id)
                );
                CREATE INDEX IF NOT EXISTS document_relationship_target
                    ON document_relationship(target_id, propagates_retention);
                """
            )
            # Additive migration for indexes created before move detection was
            # introduced. The filesystem remains the source of truth.
            columns = {row[1] for row in db.execute("PRAGMA table_info(scan_file)")}
            if "device" not in columns:
                db.execute("ALTER TABLE scan_file ADD COLUMN device INTEGER")
            if "inode" not in columns:
                db.execute("ALTER TABLE scan_file ADD COLUMN inode INTEGER")
            db.execute("CREATE INDEX IF NOT EXISTS scan_file_inode ON scan_file(device, inode)")
            listing_columns = {row[1] for row in db.execute("PRAGMA table_info(document_listing)")}
            if "version_series_id" not in listing_columns:
                db.execute("ALTER TABLE document_listing ADD COLUMN version_series_id TEXT NOT NULL DEFAULT ''")
            if "version_number" not in listing_columns:
                db.execute("ALTER TABLE document_listing ADD COLUMN version_number INTEGER NOT NULL DEFAULT 1")
            db.execute("CREATE INDEX IF NOT EXISTS document_listing_versions ON document_listing(version_series_id, version_number)")
            try:
                db.execute(
                    """CREATE VIRTUAL TABLE IF NOT EXISTS document_search
                    USING fts5(document_id UNINDEXED, path, state, tags, notes, attributes, content)"""
                )
            except sqlite3.OperationalError:
                # FTS5 is normally present in SQLite. A plain table keeps the
                # project usable on stripped-down platform builds.
                db.execute(
                    """CREATE TABLE IF NOT EXISTS document_search (
                    document_id TEXT PRIMARY KEY, path TEXT, state TEXT, tags TEXT,
                    notes TEXT, attributes TEXT, content TEXT)"""
                )
    def _recover_interrupted_collection_deletions(self) -> None:
        """Roll back a collection DELETE that stopped before it was committed."""
        recovery_root = self.control / COLLECTION_TRASH_DIR
        if not recovery_root.is_dir() or recovery_root.is_symlink():
            return
        for operation in sorted(recovery_root.iterdir()):
            manifest_path = operation / "manifest.json"
            manifest = self._read_json(manifest_path, {})
            if (
                not operation.is_dir() or operation.is_symlink()
                or manifest.get("state") in {"committed", "restored"}
            ):
                continue
            try:
                deletion_id = str(uuid.UUID(operation.name))
                if manifest.get("deletion_id") != deletion_id:
                    raise ValueError("collection recovery manifest has an invalid identity")
                relative = self._safe_managed_relative_path(
                    str(manifest.get("source", "")), require_name=True,
                )
                source = self.root / relative
                staged = operation / "tree"
                if staged.exists():
                    if staged.is_symlink() or not staged.is_dir() or source.exists():
                        raise ValueError("collection recovery cannot safely restore its namespace")
                    if not source.parent.is_dir() or source.parent.is_symlink():
                        raise ValueError("collection recovery parent is unavailable")
                    staged.replace(source)
                elif not source.is_dir() or source.is_symlink():
                    raise ValueError("collection recovery payload is unavailable")
                snapshots = manifest.get("document_snapshots", {})
                if not isinstance(snapshots, dict):
                    raise ValueError("collection recovery snapshots are invalid")
                for document_id, snapshot in snapshots.items():
                    if not isinstance(snapshot, dict) or snapshot.get("document_id") != document_id:
                        raise ValueError("collection recovery document identity is invalid")
                    self._save_document(snapshot)
                    self._refresh_search_index(snapshot)
                    restored_file = self.root / str(snapshot.get("last_path", ""))
                    if restored_file.is_file() and not restored_file.is_symlink():
                        self._scan_file(restored_file, force_hash=True)
                manifest_path.unlink(missing_ok=True)
                operation.rmdir()
                details = {
                    "deletion_id": deletion_id, "path": str(relative),
                    "documents": len(snapshots), "at": utc_now(), "actor": "system",
                }
                self._event("webdav_collection_delete_recovered", details)
                self._record_revision(
                    "webdav_collection_delete_recovered", "system", "collections",
                    hashlib.sha256(str(relative).encode()).hexdigest(), details,
                )
            except (OSError, ValueError) as exc:
                if isinstance(manifest, dict):
                    manifest["state"] = "recovery_blocked"
                    manifest["recovery_error"] = str(exc)
                    manifest["recovery_checked_at"] = utc_now()
                    atomic_json_write(manifest_path, manifest)

    def ensure_folder_policy(self, folder: str | Path, actor: str = "system") -> Path:
        folder_path = Path(folder).resolve()
        if self.root not in (folder_path, *folder_path.parents):
            raise ValueError("folder is outside the document root")
        policy = folder_path / POLICY_FILE
        if policy.exists():
            try:
                loaded = json.loads(policy.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and loaded.get("folder_id"):
                    return policy
            except (OSError, json.JSONDecodeError):
                # Do not overwrite an invalid policy. It must be repaired visibly.
                raise ValueError(f"invalid folder policy: {policy}")
        created_at = utc_now()
        atomic_json_write(
            policy,
            {
                "version": 2,
                "folder_id": str(uuid.uuid4()),
                "created_at": created_at,
                "created_by": actor,
                "inherit": True,
                "grants": [],
                "scan": {
                    "follow_symlinks": False,
                    "allow_other_filesystems": False,
                },
                "retention": {"rules": []},
            },
        )
        self._event(
            "folder_policy_created",
            {"path": self.relative(folder_path), "actor": actor, "created_at": created_at},
        )
        self._record_revision(
            "folder_policy_created",
            actor,
            "policies",
            hashlib.sha256(self.relative(folder_path).encode("utf-8")).hexdigest(),
            self._read_json(policy, {}),
        )
        return policy

    def import_file(self, source: str | Path, actor: str = "system") -> Path:
        """Copy one local file into inbox without interpreting its filename as a command."""
        self.initialize()
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file() or source_path.is_symlink():
            raise ValueError("source must be a regular file")
        inbox = self.root / "inbox"
        self.ensure_folder_policy(inbox)
        safe_name = source_path.name.replace("/", "_").replace("\\", "_")
        target = inbox / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}-{safe_name}"
        shutil.copy2(source_path, target)
        self._event("file_imported", {"source": str(source_path), "path": self.relative(target), "actor": actor})
        self.scan()
        metadata = self.get_document(target)
        self._record_revision("document_imported", actor, "documents", metadata["document_id"], metadata)
        return target

    def import_directory(self, source: str | Path, label: str, actor: str = "system") -> dict[str, int | str]:
        """Copy an existing directory into the managed archive without modifying it.

        The source tree remains untouched.  Its relative directory structure is
        retained below ``imports/<label>`` and name collisions create an
        additional file instead of replacing an earlier import.
        """
        self._require_actor(actor)
        self.initialize()
        source_root = Path(source).expanduser().resolve()
        if not source_root.is_dir() or source_root.is_symlink():
            raise ValueError("source directory must be a regular directory")
        source_in_staging = self.control == source_root or self.control in source_root.parents
        if source_root == self.root or (self.root in source_root.parents and not source_in_staging) or source_root in self.root.parents:
            raise ValueError("source directory must be outside the main archive")
        safe_label = re.sub(r"[^A-Za-z0-9._-]+", "-", label.strip()).strip(".-") or "storage"
        destination_root = self.root / "imports" / safe_label
        copied = unchanged = skipped = errors = 0
        for source_path in sorted(source_root.rglob("*"), key=lambda item: str(item).casefold()):
            try:
                if not source_path.is_file() or source_path.is_symlink():
                    skipped += 1
                    continue
                relative = source_path.relative_to(source_root)
                destination = destination_root / relative
                source_hash = sha256_file(source_path)
                if destination.is_file() and sha256_file(destination) == source_hash:
                    unchanged += 1
                    continue
                if destination.exists():
                    destination = destination.with_name(f"{destination.stem}-{source_hash[:12]}{destination.suffix}")
                    if destination.is_file() and sha256_file(destination) == source_hash:
                        unchanged += 1
                        continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                self.ensure_folder_policy(destination.parent)
                shutil.copy2(source_path, destination)
                copied += 1
            except OSError as exc:
                errors += 1
                self._event("directory_import_file_skipped", {"source": str(source_path), "error": str(exc), "actor": actor})
        self.scan()
        result: dict[str, int | str] = {"source": str(source_root), "destination": self.relative(destination_root), "copied": copied, "unchanged": unchanged, "skipped": skipped, "errors": errors}
        self._event("directory_imported", {**result, "actor": actor})
        self._record_revision("directory_imported", actor, "documents", safe_label, result)
        return result

    def import_upload(
        self,
        upload: Any,
        filename: str,
        actor: str,
        archive: bool = False,
        max_bytes: int = 512 * 1024 * 1024,
    ) -> dict[str, Any]:
        """Store an uploaded file safely; archive placement is content-hash sorted."""
        self._require_actor(actor)
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("upload size limit must be a positive number of bytes")
        self.initialize()
        safe_name = Path(filename or "upload").name.replace("/", "_").replace("\\", "_")
        if safe_name in ("", "."):
            safe_name = "upload"
        staging = self.control / "staging" / f"{uuid.uuid4().hex}-{safe_name}"
        staging.parent.mkdir(parents=True, exist_ok=True)
        source = getattr(upload, "stream", upload)
        try:
            written = 0
            with staging.open("wb") as destination:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    written += len(chunk)
                    if written > max_bytes:
                        limit_mib = max_bytes / (1024 * 1024)
                        raise ValueError(f"upload exceeds the {limit_mib:g} MiB size limit")
                    destination.write(chunk)
            digest = sha256_file(staging)
            if archive:
                destination_dir = self.root / "archive" / digest[:2] / digest
            else:
                destination_dir = self.root / "inbox"
            self.ensure_folder_policy(destination_dir)
            target = destination_dir / safe_name
            if target.exists():
                target = destination_dir / f"{target.stem}-{uuid.uuid4().hex[:8]}{target.suffix}"
            staging.replace(target)
        except Exception:
            staging.unlink(missing_ok=True)
            raise
        self.scan()
        metadata = self.get_document(target)
        self._event("file_uploaded", {"document_id": metadata["document_id"], "path": self.relative(target), "actor": actor, "sha256": digest, "archive": archive})
        self._record_revision("document_uploaded", actor, "documents", metadata["document_id"], metadata)
        return metadata

    def archives(self) -> list[dict[str, Any]]:
        self.initialize()
        return sorted(self._read_json(self.archives_path, {"archives": []}).get("archives", []), key=lambda item: item.get("label", ""))

    def main_archive(self) -> dict[str, Any]:
        """Describe the always-connected primary document archive separately."""
        self.initialize()
        policy = self._read_json(self.root / POLICY_FILE, {})
        return {"label": "Hauptarchiv (lokal)", "archive_id": policy.get("folder_id", "local"), "path": str(self.root), "available": True}

    def create_share(self, reference: str | Path, password: str, expires_days: int, actor: str, note_id: str = "") -> dict[str, Any]:
        """Create a password-protected, expiring share for a file or one note."""
        self._require_actor(actor)
        password = password.strip()
        if len(password) < 8:
            raise ValueError("share password must contain at least 8 characters")
        if not 1 <= expires_days <= 365:
            raise ValueError("share expiry must be between 1 and 365 days")
        document = self.get_document(reference)
        self._require_document_editable(document)
        note = next((item for item in document.get("notes", []) if item.get("id") == note_id), None) if note_id else None
        if note_id and note is None:
            raise ValueError("note does not exist on this document")
        share_id = uuid.uuid4().hex
        salt = os.urandom(16)
        expires_at = datetime.now(timezone.utc).replace(microsecond=0).timestamp() + expires_days * 86400
        share = {
            "share_id": share_id,
            "document_id": document["document_id"],
            "resource_type": "note" if note else "file",
            "note_id": note_id or None,
            "created_by": actor,
            "created_at": utc_now(),
            "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
            "password_salt": salt.hex(),
            "password_hash": hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1).hex(),
            "access_log": [],
        }
        payload = self._read_json(self.shares_path, {"shares": []})
        payload["shares"] = [*payload.get("shares", []), share]
        atomic_json_write(self.shares_path, payload)
        self._event("share_created", {"share_id": share_id, "document_id": document["document_id"], "actor": actor})
        self._record_revision("share_created", actor, "shares", share_id, {key: value for key, value in share.items() if not key.startswith("password_")})
        return {key: value for key, value in share.items() if not key.startswith("password_")}

    def document_shares(self, reference: str | Path) -> list[dict[str, Any]]:
        document_id = self.get_document(reference)["document_id"]
        now = datetime.now(timezone.utc)
        shares = [item for item in self._read_json(self.shares_path, {"shares": []}).get("shares", []) if item.get("document_id") == document_id]
        return [self._public_share(item, now) for item in sorted(shares, key=lambda item: item.get("created_at", ""), reverse=True)]

    def share_status(self, share_id: str) -> dict[str, Any] | None:
        share = next((item for item in self._read_json(self.shares_path, {"shares": []}).get("shares", []) if item.get("share_id") == share_id), None)
        return self._public_share(share, datetime.now(timezone.utc)) if share else None

    def renew_share(self, reference: str | Path, share_id: str, password: str, expires_days: int, actor: str) -> dict[str, Any]:
        """Reactivate the same persistent URL, only with a replacement password."""
        self._require_actor(actor)
        if len(password.strip()) < 8: raise ValueError("share password must contain at least 8 characters")
        if not 1 <= expires_days <= 365: raise ValueError("share expiry must be between 1 and 365 days")
        document_id = self.get_document(reference)["document_id"]
        payload = self._read_json(self.shares_path, {"shares": []})
        share = next((item for item in payload["shares"] if item.get("share_id") == share_id and item.get("document_id") == document_id), None)
        if share is None: raise ValueError("share does not belong to this document")
        salt = os.urandom(16)
        share.update({"password_salt": salt.hex(), "password_hash": hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1).hex(), "expires_at": datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + expires_days * 86400, timezone.utc).isoformat(), "reactivated_at": utc_now(), "reactivated_by": actor})
        atomic_json_write(self.shares_path, payload)
        self._event("share_reactivated", {"share_id": share_id, "document_id": document_id, "actor": actor})
        return self._public_share(share, datetime.now(timezone.utc))

    def open_share(self, share_id: str, password: str, remote_addr: str = "") -> dict[str, Any]:
        """Validate a share password and return its document metadata."""
        payload = self._read_json(self.shares_path, {"shares": []})
        share = next((item for item in payload["shares"] if item.get("share_id") == share_id), None)
        if not share: raise ValueError("Freigabelink ist nicht verfügbar")
        if self._share_expired(share):
            self._record_share_access(payload, share, "expired_access", remote_addr)
            raise ValueError("Freigabelink ist abgelaufen")
        expected = bytes.fromhex(share["password_hash"])
        actual = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(share["password_salt"]), n=2**14, r=8, p=1)
        if not hmac.compare_digest(expected, actual):
            self._record_share_access(payload, share, "password_rejected", remote_addr)
            raise ValueError("Passwort ist nicht korrekt")
        document = self.get_document(share["document_id"])
        self._record_share_access(payload, share, "opened", remote_addr)
        result = {"document": document, "share": {key: value for key, value in share.items() if not key.startswith("password_")}}
        if share.get("resource_type") == "note":
            note = next((item for item in document.get("notes", []) if item.get("id") == share.get("note_id")), None)
            if note is None:
                raise ValueError("the shared note is no longer available")
            return {**result, "note": note}
        path = self.root / document.get("last_path", "")
        if not path.is_file() or path.is_symlink():
            raise ValueError("the shared original is currently unavailable")
        return {**result, "path": path}

    def record_share_view(self, share_id: str, remote_addr: str = "") -> None:
        payload = self._read_json(self.shares_path, {"shares": []})
        share = next((item for item in payload["shares"] if item.get("share_id") == share_id), None)
        if share: self._record_share_access(payload, share, "link_viewed", remote_addr)

    def record_access(self, reference: str | Path, actor: str, access_type: str) -> dict[str, Any]:
        """Audit access without rewriting a potentially large document sidecar."""
        self._require_actor(actor)
        if access_type not in {"seen", "found"}: raise ValueError("unsupported document access type")
        document = self.get_document(reference)
        access = {"type": access_type, "actor": actor, "at": utc_now()}
        access_path = self.document_access / f"{document['document_id']}.json"
        from .file_lock import exclusive_file_lock
        with exclusive_file_lock(access_path.with_suffix(".lock")):
            access_metadata = self._read_json(access_path, {})
            access_metadata.setdefault("access_log", list(document.get("access_log", [])))
            access_metadata.setdefault("seen_by", dict(document.get("seen_by", {})))
            access_metadata.setdefault("found_by", dict(document.get("found_by", {})))
            access_metadata["access_log"].append(access)
            access_metadata["access_log"] = access_metadata["access_log"][-200:]
            access_metadata[f"{access_type}_by"][actor] = access["at"]
            atomic_json_write(access_path, access_metadata)
        document.update(access_metadata)
        self._event(f"document_{access_type}", {"document_id": document["document_id"], **access})
        return document

    @staticmethod
    def _share_expired(share: dict[str, Any]) -> bool:
        return datetime.fromisoformat(share["expires_at"]).astimezone(timezone.utc) < datetime.now(timezone.utc)

    def _public_share(self, share: dict[str, Any], now: datetime) -> dict[str, Any]:
        result = {key: value for key, value in share.items() if not key.startswith("password_")}
        result["status"] = "abgelaufen" if self._share_expired(share) else "aktiv"
        return result

    def _record_share_access(self, payload: dict[str, Any], share: dict[str, Any], action: str, remote_addr: str) -> None:
        entry = {"action": action, "at": utc_now(), "ip": remote_addr or "unbekannt", "share_id": share["share_id"]}
        share.setdefault("access_log", []).append(entry)
        atomic_json_write(self.shares_path, payload)
        self._event("share_access", {"share_id": share["share_id"], "document_id": share["document_id"], **entry})

    def ssh_sources(self) -> list[dict[str, Any]]:
        self.initialize()
        return self._read_json(self.ssh_sources_path, {"sources": []}).get("sources", [])

    def register_ssh_source(self, name: str, host: str, username: str, remote_path: str, key_path: str, actor: str) -> dict[str, Any]:
        """Register an SSH source without storing a password or private key."""
        self._require_actor(actor)
        values = {"name": name.strip(), "host": host.strip(), "username": username.strip(), "remote_path": remote_path.strip(), "key_path": key_path.strip()}
        if not all(values[key] for key in ("name", "host", "username", "remote_path")):
            raise ValueError("name, host, SSH user and remote path are required")
        if any("\n" in value or "\x00" in value for value in values.values()):
            raise ValueError("SSH source values must not contain control characters")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", values["host"]) or not re.fullmatch(r"[A-Za-z0-9_.-]+", values["username"]):
            raise ValueError("SSH host or user contains unsupported characters")
        if not values["remote_path"].startswith("/"):
            raise ValueError("remote SSH path must be absolute")
        record = {"source_id": str(uuid.uuid4()), **values, "created_by": actor, "created_at": utc_now(), "last_sync_at": None}
        payload = self._read_json(self.ssh_sources_path, {"sources": []})
        payload["sources"] = [*payload.get("sources", []), record]
        atomic_json_write(self.ssh_sources_path, payload)
        self._event("ssh_source_registered", {"source_id": record["source_id"], "actor": actor, "host": values["host"]})
        self._record_revision("ssh_source_registered", actor, "ssh-sources", record["source_id"], record)
        return record

    def sync_ssh_source(self, source_id: str, actor: str) -> int:
        """One-way import from an SSH source using rsync and the local SSH agent."""
        self._require_actor(actor)
        source = next((item for item in self.ssh_sources() if item.get("source_id") == source_id), None)
        if source is None:
            raise ValueError("unknown SSH source")
        if shutil.which("rsync") is None:
            raise RuntimeError("rsync is required for SSH import; on Windows use WSL or install rsync")
        staging = self.control / "ssh-staging" / source_id
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        remote = f"{source['username']}@{source['host']}:{source['remote_path'].rstrip('/')}/"
        command = ["rsync", "-a", "--no-links", "--safe-links"]
        if source.get("key_path"):
            key_path = Path(source["key_path"]).expanduser()
            if not key_path.is_file():
                raise ValueError("configured SSH key file is unavailable")
            command.extend(["-e", shlex.join(["ssh", "-i", str(key_path)])])
        command.extend(["--", remote, f"{staging}/"])
        result = subprocess.run(command, capture_output=True, text=True, timeout=3600)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "SSH import failed")
        imported = int(self.import_directory(staging, f"SSH-{source['name']}", actor)["copied"])
        payload = self._read_json(self.ssh_sources_path, {"sources": []})
        for item in payload.get("sources", []):
            if item.get("source_id") == source_id:
                item["last_sync_at"] = utc_now()
                item["last_sync_by"] = actor
        atomic_json_write(self.ssh_sources_path, payload)
        self._event("ssh_source_synced", {"source_id": source_id, "actor": actor, "imported": imported})
        self._record_revision("ssh_source_synced", actor, "ssh-sources", source_id, next(item for item in payload["sources"] if item.get("source_id") == source_id))
        return imported

    def remove_ssh_source(self, source_id: str, actor: str) -> None:
        """Remove only the local SSH source configuration, never remote files."""
        self._require_actor(actor)
        payload = self._read_json(self.ssh_sources_path, {"sources": []})
        source = next((item for item in payload.get("sources", []) if item.get("source_id") == source_id), None)
        if source is None:
            raise ValueError("unknown SSH source")
        payload["sources"] = [item for item in payload["sources"] if item.get("source_id") != source_id]
        atomic_json_write(self.ssh_sources_path, payload)
        self._event("ssh_source_removed", {"source_id": source_id, "actor": actor, "host": source.get("host", "")})
        self._record_revision("ssh_source_removed", actor, "ssh-sources", source_id, source)

    def register_external_archive(self, root: str | Path, label: str, tags: list[str], actor: str) -> dict[str, Any]:
        """Mark a mounted archive volume so it remains identifiable while absent."""
        self._require_actor(actor)
        path = Path(root).expanduser().resolve()
        if not path.is_dir():
            raise ValueError("archive root must be an existing mounted directory")
        label = label.strip() or path.name
        marker_path = path / ARCHIVE_MARKER
        marker = self._read_json(marker_path, {})
        archive_id = marker.get("archive_id", str(uuid.uuid4()))
        marker = {"version": 1, "archive_id": archive_id, "label": label, "tags": sorted(set(tags)), "registered_at": marker.get("registered_at", utc_now())}
        atomic_json_write(marker_path, marker)
        all_archives = self.archives()
        record = {**marker, "last_known_path": str(path), "available": True, "last_seen_at": utc_now()}
        all_archives = [item for item in all_archives if item.get("archive_id") != archive_id] + [record]
        atomic_json_write(self.archives_path, {"archives": all_archives})
        self._event("external_archive_registered", {"archive_id": archive_id, "actor": actor, "path": str(path)})
        self._record_revision("external_archive_registered", actor, "archives", archive_id, record)
        return record

    def discover_archives(self, actor: str) -> list[dict[str, Any]]:
        """Inspect mounted volume roots for our small portable archive marker."""
        self._require_actor(actor)
        found: dict[str, Path] = {}
        for mount in self._mounted_roots():
            marker = self._read_json(mount / ARCHIVE_MARKER, {})
            if marker.get("archive_id"):
                found[marker["archive_id"]] = mount
        archives = self.archives()
        updated: list[dict[str, Any]] = []
        for archive in archives:
            mounted = found.get(archive.get("archive_id"))
            updated.append({**archive, "available": mounted is not None, **({"last_known_path": str(mounted), "last_seen_at": utc_now()} if mounted else {})})
        atomic_json_write(self.archives_path, {"archives": updated})
        self._event("external_archives_discovered", {"actor": actor, "found": len(found)})
        self._record_revision("external_archives_discovered", actor, "archives", "registry", {"archives": updated})
        return updated

    def get_document(self, reference: str | Path) -> dict[str, Any]:
        """Return metadata by document ID or by a path inside the managed tree."""
        self.initialize()
        reference_text = str(reference)
        candidate = Path(reference).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        if candidate.exists() and candidate.is_file() and not candidate.is_symlink():
            self._scan_file(candidate.resolve())
            with self._db() as db:
                row = db.execute(
                    "SELECT document_id FROM scan_file WHERE relative_path = ?",
                    (self.relative(candidate),),
                ).fetchone()
            if row:
                reference_text = str(row[0])
        metadata = self._read_json(self.documents / f"{reference_text}.json", {})
        if not metadata.get("document_id"):
            raise ValueError(f"unknown document: {reference}")
        access_metadata = self._read_json(self.document_access / f"{metadata['document_id']}.json", {})
        for key in ("access_log", "seen_by", "found_by"):
            if key in access_metadata:
                metadata[key] = access_metadata[key]
        return metadata

    def add_note(self, reference: str | Path, text: str, author: str = "") -> dict[str, Any]:
        """Append an immutable note to a document's file-based metadata."""
        self._require_actor(author)
        text = text.strip()
        if not text:
            raise ValueError("note must not be empty")
        metadata = self.get_document(reference)
        self._require_document_editable(metadata)
        note = {"id": str(uuid.uuid4()), "text": text, "author": author, "created_at": utc_now()}
        metadata.setdefault("notes", []).append(note)
        self._save_document(metadata)
        self._write_note_snapshot(metadata, note)
        self._refresh_search_index(metadata)
        self._event("document_note_added", {"document_id": metadata["document_id"], "note_id": note["id"]})
        self._record_revision("document_note_added", author, "documents", metadata["document_id"], metadata)
        return note

    def note_snapshot(self, reference: str | Path, note_id: str) -> Path:
        """Return the immutable PDF snapshot that was created with a note."""
        document = self.get_document(reference)
        note = next((item for item in document.get("notes", []) if item.get("id") == note_id), None)
        if note is None:
            raise ValueError("note does not exist on this document")
        path = self.note_snapshots / f"{note_id}.pdf"
        if not path.exists():
            self._write_note_snapshot(document, note)
        return path

    def set_state(self, reference: str | Path, state: str, author: str = "") -> dict[str, Any]:
        """Set a human workflow state and preserve the complete state history."""
        self._require_actor(author)
        state = state.strip()
        if not state:
            raise ValueError("state must not be empty")
        metadata = self.get_document(reference)
        self._require_document_editable(metadata)
        previous = metadata.get("state", "new")
        event = {"from": previous, "to": state, "author": author, "changed_at": utc_now()}
        metadata["state"] = state
        metadata.setdefault("state_history", []).append(event)
        self._save_document(metadata)
        self._refresh_search_index(metadata)
        self._event("document_state_changed", {"document_id": metadata["document_id"], **event})
        self._record_revision("document_state_changed", author, "documents", metadata["document_id"], metadata)
        return event

    def set_attribute(self, reference: str | Path, key: str, value: str, author: str = "") -> None:
        """Set a domain-specific metadata value without schema migration."""
        self._require_actor(author)
        key = key.strip()
        if not key:
            raise ValueError("attribute key must not be empty")
        metadata = self.get_document(reference)
        self._require_document_editable(metadata)
        metadata.setdefault("attributes", {})[key] = value
        self._save_document(metadata)
        self._refresh_search_index(metadata)
        self._event(
            "document_attribute_set",
            {"document_id": metadata["document_id"], "key": key, "author": author},
        )
        self._record_revision("document_attribute_set", author, "documents", metadata["document_id"], metadata)

    def set_tags(self, reference: str | Path, tags: list[str], author: str = "") -> dict[str, Any]:
        """Replace document tags while retaining filesystem and revision metadata."""
        self._require_actor(author)
        metadata = self.get_document(reference)
        self._require_document_editable(metadata)
        previous = set(metadata.get("tags", []))
        updated = sorted({tag.strip() for tag in tags if tag.strip()}, key=str.casefold)
        tagged_at = metadata.setdefault("tagged_at", {})
        now = utc_now()
        for tag in updated:
            if tag not in previous:
                tagged_at[tag] = now
        metadata["tags"] = updated
        self._save_document(metadata)
        self._refresh_search_index(metadata)
        self._event("document_tags_set", {"document_id": metadata["document_id"], "actor": author, "tags": metadata["tags"]})
        self._record_revision("document_tags_set", author, "documents", metadata["document_id"], metadata)
        return metadata

    def export_portable_metadata(self, reference: str | Path, actor: str) -> Path:
        """Write an interoperable sidecar without renaming or modifying the file."""
        self._require_actor(actor)
        metadata = self.get_document(reference)
        path = self.root / str(metadata.get("last_path", ""))
        if not path.is_file() or path.is_symlink():
            raise ValueError("document file is unavailable")
        sidecar_dir = path.parent / CONTROL_DIR
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        sidecar = sidecar_dir / f"{path.name}.simpleoffice.json"
        payload = {
            "schema": "https://simpleoffice.local/schemas/portable-file-metadata/v1",
            "version": 1,
            "document_id": metadata["document_id"], "file_name": path.name,
            "sha256": sha256_file(path), "state": metadata.get("state", "new"),
            "tags": sorted(set(metadata.get("tags", [])), key=str.casefold),
            "description": str(metadata.get("attributes", {}).get("description", "")),
            "origin": metadata.get("attributes", {}).get("attachment_origin", {}),
            "exported_at": utc_now(),
        }
        atomic_json_write(sidecar, payload)
        self._event("portable_metadata_exported", {"document_id": metadata["document_id"], "actor": actor, "sidecar": self.relative(sidecar)})
        self._record_revision("portable_metadata_exported", actor, "documents", metadata["document_id"], payload)
        return sidecar

    def export_all_portable_metadata(self, actor: str) -> dict[str, int]:
        result = {"exported": 0, "errors": 0}
        for metadata in self._all_documents():
            try:
                self.export_portable_metadata(metadata["document_id"], actor)
                result["exported"] += 1
            except (OSError, ValueError):
                result["errors"] += 1
        self._record_revision("portable_metadata_bulk_exported", actor, "documents", "portable-metadata", result)
        return result

    def add_deadline(
        self,
        reference: str | Path,
        kind: str,
        expires_at: str,
        label: str,
        author: str,
    ) -> dict[str, Any]:
        """Append an audited document deadline without replacing older rules."""
        self._require_actor(author)
        kind = kind.strip().casefold()
        if kind not in {"retention", "work"}:
            raise ValueError("deadline kind must be retention or work")
        parsed = parse_deadline(expires_at)
        deadline = {
            "id": str(uuid.uuid4()),
            "kind": kind,
            "expires_at": parsed.isoformat(),
            "label": label.strip() or ("Aufbewahrung" if kind == "retention" else "Bearbeiten bis"),
            "created_at": utc_now(),
            "created_by": author,
        }
        metadata = self.get_document(reference)
        metadata.setdefault("deadlines", []).append(deadline)
        self._save_document(metadata)
        self._event(
            "document_deadline_added",
            {"document_id": metadata["document_id"], "actor": author, **deadline},
        )
        self._record_revision(
            "document_deadline_added", author, "documents", metadata["document_id"], metadata
        )
        return deadline

    def folder_retention_rules(self) -> list[dict[str, Any]]:
        """Return configured folder rules without following links or leaving the archive."""
        configured: list[dict[str, Any]] = []
        root_device = self.root.stat().st_dev
        policy_paths: list[Path] = []
        for current, directories, files in os.walk(self.root, followlinks=False):
            folder = Path(current)
            retained_directories: list[str] = []
            for name in directories:
                candidate = folder / name
                try:
                    if (
                        name not in {CONTROL_DIR, HISTORY_DIR}
                        and not candidate.is_symlink()
                        and candidate.stat().st_dev == root_device
                    ):
                        retained_directories.append(name)
                except OSError:
                    continue
            directories[:] = retained_directories
            if POLICY_FILE in files:
                policy_paths.append(folder / POLICY_FILE)
        for policy_path in sorted(policy_paths, key=lambda item: str(item).casefold()):
            folder = policy_path.parent
            policy = self._read_json(policy_path, {})
            retention = policy.get("retention", {})
            rules = retention.get("rules", []) if isinstance(retention, dict) else []
            for rule in rules:
                if isinstance(rule, dict):
                    configured.append({**rule, "folder": self.relative(folder)})
        return configured

    def add_folder_retention_rule(
        self,
        folder: str,
        kind: str,
        label: str,
        actor: str,
        *,
        tag: str = "",
        expires_at: str = "",
        years: int | str | None = None,
    ) -> dict[str, Any]:
        """Append one validated, audited rule to an existing archive folder."""
        self._require_actor(actor)
        target = (self.root / folder.strip()).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("folder is outside the document root") from exc
        if not target.is_dir() or target.is_symlink():
            raise ValueError("folder must be an existing regular directory")
        normalized_kind = kind.strip().casefold()
        if normalized_kind not in {"retention", "work"}:
            raise ValueError("deadline kind must be retention or work")
        normalized_label = label.strip()
        if not normalized_label:
            raise ValueError("rule label is required")
        rule: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "kind": normalized_kind,
            "label": normalized_label,
        }
        normalized_tag = tag.strip()
        if normalized_tag:
            rule["tag"] = normalized_tag
        has_date = bool(expires_at.strip())
        has_years = bool(str(years or "").strip())
        if has_date and has_years:
            raise ValueError("provide either a fixed date or years, not both")
        if has_date:
            rule["expires_at"] = parse_deadline(expires_at).isoformat()
        else:
            try:
                normalized_years = int(years or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError("years must be a whole number") from exc
            if not 1 <= normalized_years <= 100:
                raise ValueError("years must be between 1 and 100")
            rule["years"] = normalized_years

        policy_path = self.ensure_folder_policy(target)
        policy = self._read_json(policy_path, {})
        retention = policy.setdefault("retention", {})
        if not isinstance(retention, dict):
            raise ValueError("folder retention configuration is invalid")
        rules = retention.setdefault("rules", [])
        if not isinstance(rules, list):
            raise ValueError("folder retention rules are invalid")
        rules.append(rule)
        atomic_json_write(policy_path, policy)
        event = {"folder": self.relative(target), "actor": actor, "rule": rule}
        self._event("folder_retention_rule_added", event)
        self._record_revision(
            "folder_retention_rule_added", actor, "policies", policy["folder_id"], policy
        )
        return {**rule, "folder": self.relative(target)}

    def remove_folder_retention_rule(self, folder: str, rule_id: str, actor: str) -> None:
        """Remove exactly one selected rule; never alter document deadlines."""
        self._require_actor(actor)
        target = (self.root / folder.strip()).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("folder is outside the document root") from exc
        policy_path = target / POLICY_FILE
        if not target.is_dir() or target.is_symlink() or not policy_path.is_file():
            raise ValueError("folder policy does not exist")
        policy = self._read_json(policy_path, {})
        retention = policy.get("retention", {})
        rules = retention.get("rules", []) if isinstance(retention, dict) else []
        remaining = [rule for rule in rules if not isinstance(rule, dict) or rule.get("id") != rule_id]
        if len(remaining) == len(rules):
            raise ValueError("retention rule does not exist")
        retention["rules"] = remaining
        atomic_json_write(policy_path, policy)
        self._event(
            "folder_retention_rule_removed",
            {"folder": self.relative(target), "actor": actor, "rule_id": rule_id},
        )
        self._record_revision(
            "folder_retention_rule_removed", actor, "policies", policy["folder_id"], policy
        )

    def retention_status(
        self,
        reference: str | Path,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Evaluate only the indexed retention component of one document."""
        focus = self.get_document(reference)
        # Keep the requested document current even while a background index is
        # still filling the disposable projection.
        self._refresh_listing_index(focus)
        with self._db() as db:
            rows = db.execute(
                """WITH RECURSIVE connected(document_id) AS (
                       VALUES (?)
                       UNION
                       SELECT relation.target_id
                         FROM document_relationship AS relation
                         JOIN connected ON relation.source_id = connected.document_id
                        WHERE relation.propagates_retention = 1
                       UNION
                       SELECT relation.source_id
                         FROM document_relationship AS relation
                         JOIN connected ON relation.target_id = connected.document_id
                        WHERE relation.propagates_retention = 1
                   )
                   SELECT document_id FROM connected""",
                (focus["document_id"],),
            ).fetchall()
        documents: dict[str, dict[str, Any]] = {focus["document_id"]: focus}
        for (document_id,) in rows:
            if document_id in documents:
                continue
            metadata = self._read_json(self.documents / f"{document_id}.json", {})
            if metadata.get("document_id"):
                documents[document_id] = metadata
        return self._retention_statuses(documents, now=now)[focus["document_id"]]

    def retention_statuses(
        self, *, now: datetime | None = None
    ) -> dict[str, dict[str, Any]]:
        """Evaluate the complete archive in one graph pass for large stores."""
        documents = {item["document_id"]: item for item in self._all_documents()}
        return self._retention_statuses(documents, now=now)

    def _retention_statuses(
        self,
        documents: dict[str, dict[str, Any]],
        *,
        now: datetime | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Evaluate supplied documents without discovering any other sidecars."""
        evaluated = {
            document_id: evaluate_deadlines(document, self._deadline_rules(document), now=now)
            for document_id, document in documents.items()
        }
        adjacency: dict[str, set[str]] = {document_id: set() for document_id in documents}
        for document in documents.values():
            source_id = document["document_id"]
            for link in document.get("relationships", []):
                target_id = str(link.get("target_document_id", ""))
                if link.get("propagates_retention") is True and target_id in documents:
                    adjacency[source_id].add(target_id)
                    adjacency[target_id].add(source_id)

        statuses: dict[str, dict[str, Any]] = {}
        unseen = set(documents)
        while unseen:
            first = next(iter(unseen))
            component: set[str] = set()
            pending = [first]
            while pending:
                document_id = pending.pop()
                if document_id in component:
                    continue
                component.add(document_id)
                pending.extend(adjacency.get(document_id, ()))
            unseen.difference_update(component)

            retention_findings: list[dict[str, Any]] = []
            errors: list[dict[str, Any]] = []
            missing_deadlines: list[str] = []
            for document_id in sorted(component):
                document = documents[document_id]
                result = evaluated[document_id]
                document_retention = [
                    {
                        **finding,
                        "document_id": document_id,
                        "document_path": document.get("last_path", ""),
                    }
                    for finding in result["findings"]
                    if finding["kind"] == "retention"
                ]
                if not document_retention:
                    missing_deadlines.append(document_id)
                retention_findings.extend(document_retention)
                errors.extend({**error, "document_id": document_id} for error in result["errors"])

            retention_until = max(
                (parse_deadline(item["expires_at"]) for item in retention_findings),
                default=None,
            )
            all_expired = bool(retention_findings) and all(
                item["expired"] for item in retention_findings
            )
            cleanup_eligible = all_expired and not errors and not missing_deadlines
            for document_id in component:
                own = evaluated[document_id]
                if own["work_locked"]:
                    state = "locked"
                elif cleanup_eligible:
                    state = "cleanup_ready"
                elif missing_deadlines or errors:
                    state = "deadline_missing"
                else:
                    state = "active"
                statuses[document_id] = {
                    **own,
                    "status": state,
                    "component_document_ids": sorted(component),
                    "missing_retention_document_ids": missing_deadlines,
                    "retention_findings": sorted(
                        retention_findings,
                        key=lambda item: (item["expires_at"], item["document_id"]),
                    ),
                    "retention_until": retention_until.isoformat() if retention_until else None,
                    "all_retention_expired": all_expired,
                    "cleanup_eligible": cleanup_eligible,
                    "errors": errors,
                }
        return statuses

    def cleanup_candidates(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        """List files eligible for a manually started cleanup; never move them."""
        candidates: list[dict[str, Any]] = []
        documents = {item["document_id"]: item for item in self._all_documents()}
        for document_id, status in self.retention_statuses(now=now).items():
            document = documents[document_id]
            if status["cleanup_eligible"] and document.get("cleanup_state") != "staged":
                candidates.append(
                    {
                        "document_id": document_id,
                        "path": document.get("last_path", ""),
                        "retention_until": status["retention_until"],
                    }
                )
        return sorted(candidates, key=lambda item: (item["retention_until"], item["path"]))

    def cleanup_expired(
        self,
        destination_folder: str,
        actor: str,
        *,
        apply: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Preview or manually move eligible files; physical deletion is never performed."""
        self._require_actor(actor)
        candidates = self.cleanup_candidates(now=now)
        if not apply:
            return {"applied": False, "candidates": candidates, "moved": []}
        moved: list[dict[str, Any]] = []
        for candidate in candidates:
            relative_parent = Path(candidate["path"]).parent
            target_folder = Path(destination_folder) / (
                relative_parent if str(relative_parent) != "." else Path()
            )
            document = self.move_document(
                candidate["document_id"], str(target_folder), actor, allow_locked=True
            )
            document["cleanup_state"] = "staged"
            document["cleanup_staged_at"] = utc_now()
            document["cleanup_staged_by"] = actor
            document["cleanup_original_path"] = candidate["path"]
            self._save_document(document)
            moved.append(
                {
                    "document_id": document["document_id"],
                    "from": candidate["path"],
                    "to": document["last_path"],
                    "sha256": document.get("sha256", ""),
                    "retention_until": candidate["retention_until"],
                }
            )
        self._event(
            "retention_cleanup_completed",
            {"actor": actor, "destination": destination_folder, "moved": moved},
        )
        self._record_revision(
            "retention_cleanup_completed",
            actor,
            "retention",
            "cleanup",
            {"at": utc_now(), "destination": destination_folder, "moved": moved},
        )
        return {"applied": True, "candidates": candidates, "moved": moved}

    def analyze_image(self, reference: str | Path, author: str) -> dict[str, Any]:
        """Extract EXIF and OCR text from one managed image and add safe tags."""
        self._require_actor(author)
        metadata = self.get_document(reference)
        self._require_document_editable(metadata)
        path = self.root / metadata.get("last_path", "")
        if not self._is_image(path):
            raise ValueError("document is not a supported image")
        self._apply_image_analysis(path, metadata, force=True)
        self._save_document(metadata)
        self._refresh_search_index(metadata)
        self._event("image_analyzed", {"document_id": metadata["document_id"], "actor": author})
        self._record_revision("image_analyzed", author, "documents", metadata["document_id"], metadata)
        return metadata

    def refresh_missing_text(self, actor: str, force: bool = False) -> int:
        """Backfill searchable text for existing files without reprocessing it later."""
        self._require_actor(actor)
        pending = {
            item["document_id"] for item in self._all_documents()
            if force or item.get("text_extraction", {}).get("source_sha256") != item.get("sha256") or "extracted_text" not in item
        }
        self.scan()
        updated = 0
        for metadata in self._all_documents():
            path = self.root / metadata.get("last_path", "")
            if not path.is_file() or path.is_symlink():
                continue
            if self._is_image(path):
                self._apply_image_analysis(path, metadata, force=force)
            changed = self._apply_document_text_extraction(path, metadata, force=force)
            if changed or metadata["document_id"] in pending:
                metadata.setdefault("text_extraction", {})["updated_by"] = actor
                self._save_document(metadata)
                self._refresh_search_index(metadata)
                updated += 1
        self._event("document_text_backfill", {"actor": actor, "updated": updated, "force": force})
        self._record_revision("document_text_backfill", actor, "search", "text-backfill", {"updated": updated, "force": force, "at": utc_now()})
        return updated

    def search_page(self, query: str, page: int = 1, page_size: int = 25) -> dict[str, Any]:
        """Return one fast, indexed result page without touching document files."""
        query = query.strip()
        if not query:
            return {"results": [], "page": 1, "page_size": page_size, "has_next": False}
        compiled = compile_query(query)
        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        limit = page_size + 1
        offset = (page - 1) * page_size
        self.initialize()
        with self._db() as db:
            try:
                rows = db.execute(
                    "SELECT document_id, path, state FROM document_search WHERE document_search MATCH ? LIMIT ? OFFSET ?",
                    (compiled.fts, limit, offset),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = db.execute(
                    f"SELECT document_id, path, state FROM document_search WHERE {compiled.where} LIMIT ? OFFSET ?",
                    (*compiled.parameters, limit, offset),
                ).fetchall()
        results = [{"document_id": row[0], "path": row[1], "state": row[2]} for row in rows]
        return {
            "results": results[:page_size],
            "page": page,
            "page_size": page_size,
            "has_next": len(results) > page_size,
        }

    def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Compatibility helper returning the first indexed search page."""
        return self.search_page(query, page_size=limit)["results"]

    def add_link(
        self,
        source: str | Path,
        target: str | Path,
        relation_type: str = "related",
        label: str = "",
        author: str = "",
        propagates_retention: bool = False,
    ) -> dict[str, Any]:
        """Create a directed, labelled document relationship for graph views."""
        self._require_actor(author)
        source_metadata = self.get_document(source)
        self._require_document_editable(source_metadata)
        target_metadata = self.get_document(target)
        if source_metadata["document_id"] == target_metadata["document_id"]:
            raise ValueError("a document cannot be linked to itself")
        relation_type = relation_type.strip() or "related"
        for link in source_metadata.setdefault("relationships", []):
            if link.get("target_document_id") == target_metadata["document_id"] and link.get("type") == relation_type:
                return link
        link = {
            "id": str(uuid.uuid4()),
            "target_document_id": target_metadata["document_id"],
            "type": relation_type,
            "label": label.strip(),
            "propagates_retention": propagates_retention is True,
            "author": author,
            "created_at": utc_now(),
        }
        source_metadata["relationships"].append(link)
        self._save_document(source_metadata)
        self._refresh_search_index(source_metadata)
        self._event(
            "document_link_added",
            {"source_document_id": source_metadata["document_id"], "target_document_id": target_metadata["document_id"], "type": relation_type},
        )
        self._record_revision("document_link_added", author, "documents", source_metadata["document_id"], source_metadata)
        return link

    def add_text_link(self, source: str | Path, target_text: str, relation_type: str = "related", label: str = "", author: str = "") -> dict[str, Any]:
        """Link a document to a durable free-text reference such as an URL or case number."""
        self._require_actor(author)
        target_text = target_text.strip()
        if not target_text:
            raise ValueError("free-text reference must not be empty")
        source_metadata = self.get_document(source)
        self._require_document_editable(source_metadata)
        relation_type = relation_type.strip() or "related"
        link = {"id": str(uuid.uuid4()), "target_text": target_text, "type": relation_type, "label": label.strip(), "author": author, "created_at": utc_now()}
        source_metadata.setdefault("relationships", []).append(link)
        self._save_document(source_metadata)
        self._refresh_search_index(source_metadata)
        self._event("document_text_link_added", {"source_document_id": source_metadata["document_id"], "target_text": target_text, "type": relation_type})
        self._record_revision("document_text_link_added", author, "documents", source_metadata["document_id"], source_metadata)
        return link

    def import_version(self, source: str | Path, version_of: str | Path, author: str = "") -> dict[str, Any]:
        """Import SOURCE as the next version of an existing document."""
        self._require_actor(author)
        parent = self.get_document(version_of)
        target = self.import_file(source, author)
        self.scan()
        version = self.get_document(target)
        series_id = parent.get("version_series_id", parent["document_id"])
        version_numbers = [
            int(item.get("version_number", 1))
            for item in self._all_documents()
            if item.get("version_series_id", item.get("document_id")) == series_id
        ]
        version.update(
            {
                "version_series_id": series_id,
                "version_number": max(version_numbers, default=1) + 1,
                "version_of": parent["document_id"],
                "state": "new_version",
            }
        )
        version.setdefault("state_history", []).append(
            {"from": "new", "to": "new_version", "author": author, "changed_at": utc_now()}
        )
        self._save_document(version)
        self._refresh_search_index(version)
        self.add_link(version["document_id"], parent["document_id"], "version_of", "Vorgängerversion", author)
        self._event(
            "document_version_imported",
            {"document_id": version["document_id"], "version_of": parent["document_id"], "version_number": version["version_number"]},
        )
        self._record_revision("document_version_imported", author, "documents", version["document_id"], version)
        return version

    def replace_content(
        self,
        reference: str | Path,
        content: bytes,
        author: str,
        *,
        expected_sha256: str = "",
        source: str = "webdav",
        max_bytes: int = 512 * 1024 * 1024,
        restored_from_sha256: str = "",
    ) -> dict[str, Any]:
        """Atomically replace a managed file and retain the previous payload.

        The precondition is checked while holding the same filesystem lock as
        the write. This makes an HTTP ETag useful even when two WebDAV workers
        receive concurrent saves.
        """
        self._require_actor(author)
        if len(content) > max_bytes:
            raise ValueError("document exceeds the configured upload size limit")
        self.initialize()
        from .file_lock import exclusive_file_lock

        with exclusive_file_lock(self.control / ".document-content.lock"):
            metadata = self.get_document(reference)
            self._require_document_editable(metadata)
            path = self.root / str(metadata.get("last_path", ""))
            if not path.is_file() or path.is_symlink():
                raise ValueError("document file is unavailable")
            current_sha256 = sha256_file(path)
            if expected_sha256 and not hmac.compare_digest(expected_sha256, current_sha256):
                raise ValueError("document content changed since it was opened")
            new_sha256 = hashlib.sha256(content).hexdigest()
            if hmac.compare_digest(current_sha256, new_sha256):
                return metadata

            now = utc_now()
            archive = self.control / "content-versions" / metadata["document_id"] / current_sha256
            archive.parent.mkdir(parents=True, exist_ok=True)
            if not archive.exists():
                temporary_archive = archive.with_suffix(".partial")
                shutil.copy2(path, temporary_archive)
                if sha256_file(temporary_archive) != current_sha256:
                    temporary_archive.unlink(missing_ok=True)
                    raise RuntimeError("previous document version could not be verified")
                temporary_archive.replace(archive)

            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
            try:
                with temporary.open("xb") as destination:
                    destination.write(content)
                    destination.flush()
                    os.fsync(destination.fileno())
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)

            revision = {
                "number": int(metadata.get("content_revision", 0)) + 1,
                "at": now,
                "actor": author,
                "source": source,
                "previous_sha256": current_sha256,
                "sha256": new_sha256,
                "previous_size": archive.stat().st_size,
                "size": len(content),
                "archive": str(archive.relative_to(self.control)),
            }
            metadata["sha256"] = new_sha256
            metadata["content_sha256"] = new_sha256
            metadata["original_sha256"] = new_sha256
            metadata["content_revision"] = revision["number"]
            metadata["last_seen_at"] = now
            metadata["system_state"] = "indexed"
            metadata.setdefault("content_history", []).append(revision)
            metadata["content_history"] = metadata["content_history"][-200:]
            restoration = None
            if restored_from_sha256:
                restoration = {
                    "at": now,
                    "actor": author,
                    "from_sha256": current_sha256,
                    "restored_sha256": restored_from_sha256,
                    "content_revision": revision["number"],
                }
                metadata.setdefault("content_recovery_history", []).append(restoration)
                metadata["content_recovery_history"] = metadata["content_recovery_history"][-200:]
            self._write_xattrs(path, metadata["document_id"], new_sha256, metadata.get("tags", []))
            self._apply_document_text_extraction(path, metadata, force=True)
            self._save_document(metadata)
            self._refresh_search_index(metadata)
            stat = path.stat()
            with self._db() as db:
                db.execute(
                    """UPDATE scan_file SET sha256 = ?, size = ?, modified_ns = ?,
                       device = ?, inode = ?, last_seen_at = ? WHERE relative_path = ?""",
                    (new_sha256, stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino, now, self.relative(path)),
                )
            relative_path = self.relative(path)
            old_fingerprint_path = self.fingerprints / f"{current_sha256}.json"
            old_fingerprint = self._read_json(old_fingerprint_path, {})
            if old_fingerprint:
                old_fingerprint["paths"] = sorted(set(old_fingerprint.get("paths", [])) - {relative_path})
                old_fingerprint["last_seen_at"] = now
                atomic_json_write(old_fingerprint_path, old_fingerprint)
            new_fingerprint_path = self.fingerprints / f"{new_sha256}.json"
            new_fingerprint = self._read_json(new_fingerprint_path, {})
            new_fingerprint.update({
                "sha256": new_sha256,
                "first_seen_at": new_fingerprint.get("first_seen_at", now),
                "last_seen_at": now,
                "paths": sorted({*new_fingerprint.get("paths", []), relative_path}),
                "seen_count": int(new_fingerprint.get("seen_count", 0)) + 1,
            })
            atomic_json_write(new_fingerprint_path, new_fingerprint)
            self._event("document_content_replaced", {"document_id": metadata["document_id"], **revision})
            self._record_revision("document_content_replaced", author, "documents", metadata["document_id"], metadata)
            if restoration:
                self._event("document_content_restored", {"document_id": metadata["document_id"], **restoration})
                self._record_revision("document_content_restored", author, "documents", metadata["document_id"], metadata)
            return metadata

    def content_recovery_versions(self, reference: str | Path) -> list[dict[str, Any]]:
        """List immutable archived payloads that can replace the current content."""
        metadata = self.get_document(reference)
        versions: dict[str, dict[str, Any]] = {}
        for change in metadata.get("content_history", []):
            digest = str(change.get("previous_sha256", ""))
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                continue
            archive = self.control / "content-versions" / metadata["document_id"] / digest
            versions[digest] = {
                "sha256": digest,
                "created_at": str(change.get("at", "")),
                "replaced_by": str(change.get("actor", "")),
                "size": int(change.get("previous_size", 0)),
                "available": archive.is_file() and not archivny��-�G����ƭy�e"]))
            flash(f"{form['name']} gespeichert.")
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for("documents.form_records", form_id=form_id))
    records = _forms().records(form_id)
    return render_template("documents/form_records.html", form=form, records=records,
                           relation_choices=_form_relation_choices(form, str(g.user["username"])),
                           invoice_products=_invoice_products() if form.get("layout") == "invoice" else [])


@bp.route("/forms/<form_id>/<record_id>", methods=("GET", "POST"))
@login_required
def form_record_detail(form_id: str, record_id: str):
    try:
        form = _forms().definition(form_id)
        record = _forms().record(form_id, record_id)
    except ValueError:
        abort(404)
    if request.method == "POST":
        try:
            record = _forms().save_record(form_id, request.form.to_dict(), str(g.user["username"]), record_id)
            flash("Formular gespeichert. Der vorherige Stand bleibt in der Historie.")
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for("documents.form_record_detail", form_id=form_id, record_id=record_id))
    return render_template("documents/form_record_detail.html", form=form, record=record,
                           relation_choices=_form_relation_choices(form, str(g.user["username"])),
                           invoice_products=_invoice_products() if form.get("layout") == "invoice" else [])


@bp.post("/forms/definitions")
@login_required
def save_form_definition():
    try:
        definition = json.loads(request.form.get("definition", "{}"))
        form = _forms().save_definition(definition, str(g.user["username"]))
        flash(f"Formularvorlage {form['name']} gespeichert.")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        flash(f"Formularvorlage ungültig: {exc}")
    return redirect(url_for("documents.forms"))


@bp.get("/contacts/<contact_id>")
@login_required
def contact_detail(contact_id: str):
    actor = str(g.user["username"])
    try:
        contact = _contacts().get(contact_id, actor)
    except ValueError:
        abort(404)
    users = [row["username"] for row in get_db().execute("SELECT username FROM user ORDER BY username COLLATE NOCASE").fetchall()]
    return render_template("documents/contact_detail.html", contact=contact, users=users, is_owner=not contact.get("owner") or contact.get("owner") == actor)


@bp.post("/contacts")
@login_required
def save_contact():
    contact_id = request.form.get("contact_id", "")
    try:
        contact = _contacts().upsert(request.form.to_dict(), str(g.user["username"]), contact_id)
        flash("Kontakt gespeichert.")
    except ValueError as exc:
        flash(str(exc))
        contact = None
    if contact_id and contact is not None:
        return redirect(url_for("documents.contact_detail", contact_id=contact["contact_id"]))
    return redirect(url_for("documents.contacts"))


@bp.get("/contacts/export.vcf")
@login_required
def export_contacts():
    payload = _contacts().export_vcards(str(g.user["username"])).encode("utf-8")
    return send_file(io.BytesIO(payload), as_attachment=True, download_name="simpleoffice-kontakte.vcf", mimetype="text/vcard; charset=utf-8")


@bp.post("/contacts/import")
@login_required
def import_contacts():
    uploaded = request.files.get("contacts_file")
    if uploaded is None or not uploaded.filename:
        flash("Bitte eine .vcf-Datei auswählen.")
        return redirect(url_for("documents.contacts"))
    try:
        imported = _contacts().import_vcards(uploaded.read().decode("utf-8-sig"), str(g.user["username"]))
        flash(f"{imported} Kontakt(e) importiert.")
    except (UnicodeDecodeError, ValueError) as exc:
        flash(f"Kontaktimport fehlgeschlagen: {exc}")
    return redirect(url_for("documents.contacts"))


@bp.post("/contacts/<contact_id>/addresses")
@login_required
def add_contact_address(contact_id: str):
    try:
        _contacts().add_address(contact_id, request.form.get("label", ""), request.form.get("address", ""), str(g.user["username"]))
        flash("Adresse gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.contact_detail", contact_id=contact_id))


@bp.post("/contacts/<contact_id>/sharing")
@login_required
def share_contact(contact_id: str):
    actor = str(g.user["username"])
    valid_users = {row["username"] for row in get_db().execute("SELECT username FROM user").fetchall()}
    managers = request.form.getlist("managers")
    unknown = sorted(set(managers) - valid_users)
    try:
        if unknown:
            raise ValueError(f"unknown users: {', '.join(unknown)}")
        _contacts().share(contact_id, managers, actor)
        flash("Verwaltungsfreigabe gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.contact_detail", contact_id=contact_id))


@bp.post("/contacts/schema")
@login_required
def save_contact_schema():
    try:
        aliases = json.loads(request.form.get("aliases", "{}"))
        if not isinstance(aliases, dict) or not all(isinstance(value, list) for value in aliases.values()):
            raise ValueError("aliases must be a JSON object whose values are lists")
        _contacts().save_schema(request.form.get("required", "").split(","), aliases, str(g.user["username"]))
        flash("Kontaktfeld-Zuordnung gespeichert.")
    except (json.JSONDecodeError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.contacts"))


@bp.post("/contacts/carddav")
@login_required
def activate_carddav():
    try:
        _contacts().activate_carddav(str(g.user["username"]), request.form.get("password", ""), str(g.user["username"]))
        endpoint = url_for("carddav.endpoint", path=f"addressbooks/{g.user['username']}/default/", _external=True)
        flash(f"CardDAV aktiviert. Thunderbird-URL: {endpoint}")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.contacts"))


@bp.route("/calendar")
@login_required
def calendar():
    actor = str(g.user["username"])
    reminder_now = datetime.now(timezone.utc)
    try:
        reminders = _calendar().due_alarms(actor, reminder_now - timedelta(hours=12), reminder_now + timedelta(days=7))
    except ValueError as exc:
        reminders = []
        flash(f"Erinnerungen konnten nicht berechnet werden: {exc}")
    calendars = _calendars().calendars(actor)
    calendar_map = {item["calendar_id"]: item for item in calendars}
    requested_month = request.args.get("month", date.today().strftime("%Y-%m"))
    try:
        shown_month = date.fromisoformat(f"{requested_month}-01")
    except ValueError:
        shown_month = date.today().replace(day=1)
    events_by_day: dict[int, list[dict]] = {}
    visible_events = _calendar().events(actor)
    deleted_events = sorted(
        (event for event in visible_events if event.get("status") == "deleted"),
        key=lambda event: event.get("status_changed_at") or event.get("updated_at") or "",
        reverse=True,
    )
    events = [event for event in visible_events if event.get("status", "active") not in {"cancelled", "deleted", "moved"}]
    month_lower = datetime(shown_month.year, shown_month.month, 1, tzinfo=timezone.utc)
    next_month = (shown_month.replace(day=28) + timedelta(days=4)).replace(day=1)
    month_upper = datetime(next_month.year, next_month.month, 1, tzinfo=timezone.utc)
    try:
        occurrences = _calendar().occurrences(actor, month_lower, month_upper)
    except ValueError as exc:
        occurrences = []; flash(f"Serientermine konnten nicht dargestellt werden: {exc}")
    for event in events:
        collection = calendar_map.get(event.get("calendar_id") or "default", {"name": "Persönlich", "color": "#2563eb"})
        event["calendar_name"] = collection["name"]; event["calendar_color"] = collection["color"]
    for event in occurrences:
        collection = calendar_map.get(event.get("calendar_id") or "default", {"name": "Persönlich", "color": "#2563eb"})
        event["calendar_name"] = collection["name"]; event["calendar_color"] = collection["color"]
        try:
            event_day = datetime.fromisoformat(event["start"].replace("Z", "+00:00")).date()
        except (KeyError, ValueError):
            continue
        if event_day.year == shown_month.year and event_day.month == shown_month.month:
            events_by_day.setdefault(event_day.day, []).append(event)
    previous = (shown_month.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    following = (shown_month.replace(day=28) + timedelta(days=4)).replace(day=1).strftime("%Y-%m")
    users = [row["username"] for row in get_db().execute("SELECT username FROM user ORDER BY username COLLATE NOCASE").fetchall()]
    for event in events:
        # Events created before calendar sharing was introduced do not have
        # these fields.  Normalize only the in-memory view so opening the
        # calendar stays backwards compatible without rewriting user data.
        event["access"] = event.get("access") if isinstance(event.get("access"), dict) else {}
        event["managers"] = event.get("managers") if isinstance(event.get("managers"), list) else []
        if event.get("requester_email") or event.get("source") == "external_booking":
            event["origin"] = "external"; event["origin_label"] = "Externe Buchung"; event["origin_class"] = "text-bg-warning"
        elif event.get("source_uid") or event.get("source") == "ical_import":
            event["origin"] = "imported"; event["origin_label"] = "Importiert"; event["origin_class"] = "text-bg-secondary"
        elif event.get("owner") and event.get("owner") != actor:
            event["origin"] = "shared"; event["origin_label"] = f"Von {event['owner']}"; event["origin_class"] = "text-bg-info"
        else:
            event["origin"] = "own"; event["origin_label"] = "Von mir angelegt"; event["origin_class"] = "text-bg-primary"
        event["can_edit"] = _calendar()._can_edit(event, actor)
        event["is_owner"] = (event.get("owner") or actor) == actor
        event["access_role"] = "owner" if event["is_owner"] else event.get("access", {}).get(actor, "edit" if actor in event.get("managers", []) else "read")
        if event.get("status") == "confirmed" and event.get("requester_email"):
            ics_url = url_for("documents.download_booking_confirmation", event_id=event["event_id"], _external=True)
            subject = f"Terminbestätigung: {event['title']}"
            body = f"Hallo {event.get('requester_name') or ''},\n\ndein Termin wurde bestätigt. Die Kalendereinladung kannst du hier herunterladen:\n{ics_url}\n"
            event["confirmation_mailto"] = "mailto:" + event["requester_email"] + "?" + urlencode({"subject": subject, "body": body})
    return render_template("documents/calendar.html", events=events, deleted_events=deleted_events, calendars=calendars, contacts=_contacts().contacts(actor), users=users, current_username=actor, current_user_email=str(g.user["email"] or ""), local_calendar_address=local_calendar_address(actor), scheduling_access=_scheduling_access().get(actor), google_sync=_google_calendar().status(actor), booking=_calendar().booking_settings(), pending=_calendar().pending_bookings(), itip_messages=_itip().messages(actor), reminders=reminders, reminder_now=reminder_now.isoformat(timespec="seconds"), defaults=_settings().settings(), calendar_weeks=monthcalendar(shown_month.year, shown_month.month), calendar_events=events_by_day, shown_month=shown_month.strftime("%Y-%m"), shown_month_name=f"{month_name[shown_month.month]} {shown_month.year}", previous_month=previous, following_month=following)


@bp.post("/calendar/google/preview")
@login_required
def preview_google_calendar_sync():
    actor = str(g.user["username"])
    try:
        status = _google_calendar().status(actor)
        _calendars().get(status["target_calendar_id"], actor, write=True)
        result = _google_calendar().synchronize(actor, apply=False)
        flash(f"Google-Vorschau: {result['received']} Änderungen empfangen, {result['applicable']} anwendbar, {len(result['conflicts'])} Konflikte. Kalenderdaten und Sync-Token blieben unverändert.")
    except (GoogleCalendarError, ValueError) as exc:
        flash(f"Google-Kalender konnte nicht geprüft werden: {exc}")
    return redirect(url_for("documents.calendar") + "#google-calendar-sync")


@bp.post("/calendar/google/sync")
@login_required
def apply_google_calendar_sync():
    actor = str(g.user["username"])
    try:
        status = _google_calendar().status(actor)
        _calendars().get(status["target_calendar_id"], actor, write=True)
        result = _google_calendar().synchronize(actor, apply=True)
        if result["conflicts"]:
            flash(f"Google-Abgleich: {result['applied']} Änderungen gespeichert; {len(result['conflicts'])} lokale Konflikte blieben unverändert. Bitte zuerst manuell auflösen.")
        else:
            flash(f"Google-Abgleich abgeschlossen: {result['applied']} Änderungen gespeichert, keine Konflikte.")
    except (GoogleCalendarError, ValueError) as exc:
        flash(f"Google-Kalender wurde nicht geändert: {exc}")
    return redirect(url_for("documents.calendar") + "#google-calendar-sync")


@bp.post("/calendar/google/conflicts/<strategy>")
@login_required
def resolve_google_calendar_conflicts(strategy: str):
    actor = str(g.user["username"])
    try:
        status = _google_calendar().status(actor)
        _calendars().get(status["target_calendar_id"], actor, write=True)
        result = _google_calendar().synchronize(actor, apply=True, conflict_policy=strategy)
        flash(f"Google-Konflikte aufgelöst: {result['applied']} Google-Versionen übernommen, {result['kept_local']} lokale Versionen beibehalten.")
    except (GoogleCalendarError, ValueError) as exc:
        flash(f"Google-Konflikte wurden nicht aufgelöst: {exc}")
    return redirect(url_for("documents.calendar") + "#google-calendar-sync")


@bp.post("/calendar/google/reset")
@login_required
def reset_google_calendar_sync():
    _google_calendar().disable(str(g.user["username"]))
    flash("Google-Sync-Zustand entfernt. Importierte Termine bleiben erhalten; der nächste Abgleich prüft den Kalender vollständig.")
    return redirect(url_for("documents.calendar") + "#google-calendar-sync")


@bp.post("/calendar/scheduling/import")
@login_required
def import_itip_message():
    uploaded = request.files.get("itip_file")
    try:
        if uploaded is None or not uploaded.filename:
            raise ValueError("Bitte eine iTIP-/ICS-Datei auswählen.")
        payload = uploaded.stream.read(MAX_MESSAGE_BYTES + 1)
        if len(payload) > MAX_MESSAGE_BYTES:
            raise ValueError("iTIP message exceeds 1 MiB")
        message = _itip().receive(payload.decode("utf-8-sig"), str(g.user["username"]), "file-import")
        flash(f"{message['method']}-Nachricht geprüft und zur Bestätigung vorgemerkt.")
    except (UnicodeDecodeError, ValueError) as exc:
        flash(f"Termin-Nachricht abgewiesen: {exc}")
    return redirect(url_for("documents.calendar") + "#scheduling")


@bp.post("/calendar/scheduling/<message_id>/apply")
@login_required
def apply_itip_message(message_id: str):
    try:
        _itip().apply(message_id, str(g.user["username"]), request.form.get("calendar_id", "default"))
        flash("Termin-Nachricht angewendet und revisionssicher protokolliert.")
    except (ItipConflict, ValueError) as exc:
        flash(f"Termin-Nachricht konnte nicht angewendet werden: {exc}")
    return redirect(url_for("documents.calendar") + "#scheduling")


@bp.post("/calendar/scheduling/<message_id>/reject")
@login_required
def reject_itip_message(message_id: str):
    try:
        _itip().reject(message_id, str(g.user["username"]), request.form.get("reason", ""))
        flash("Termin-Nachricht abgelehnt; Kalenderdaten blieben unverändert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar") + "#scheduling")


@bp.get("/calendar/<event_id>/scheduling.ics")
@login_required
def export_itip_message(event_id: str):
    method = request.args.get("method", "REQUEST")
    try:
        payload = _itip().export(event_id, str(g.user["username"]), method, request.args.get("attendee", ""), request.args.get("partstat", ""), str(g.user["email"] or ""))
    except ValueError as exc:
        return Response(str(exc), 403, {"Content-Type": "text/plain; charset=utf-8"})
    return send_file(io.BytesIO(payload.encode()), as_attachment=True, download_name=f"termin-{method.casefold()}-{event_id}.ics", mimetype=f"text/calendar; method={method.upper()}; charset=utf-8")


@bp.post("/calendar/scheduling/access")
@login_required
def update_caldav_scheduling_access():
    actor = str(g.user["username"])
    users = {str(row["username"]) for row in get_db().execute("SELECT username FROM user").fetchall()}
    try:
        _scheduling_access().update(
            actor,
            request.form.get("enabled") == "1",
            [username for username in users if request.form.get(f"messages_{username}") == "1"],
            [username for username in users if request.form.get(f"freebusy_{username}") == "1"],
            users,
        )
        flash("CalDAV-Terminplanung und Freigaben wurden gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar") + "#scheduling-access")


@bp.post("/calendar/caldav")
@login_required
def activate_caldav():
    actor = str(g.user["username"])
    try:
        _calendars().activate(actor, request.form.get("password", ""), actor)
        flash(f"CalDAV aktiviert. Thunderbird-URL: {url_for('caldav.endpoint', path='', _external=True)}")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar") + "#caldav")


@bp.post("/calendar/collections")
@login_required
def create_calendar_collection():
    actor = str(g.user["username"])
    try:
        _calendars().create(request.form.get("name", ""), actor, request.form.get("color", "#2563eb"), request.form.get("timezone", "Europe/Berlin"), request.form.get("description", ""))
        flash("Kalender angelegt.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar") + "#caldav")


@bp.post("/calendar/collections/<calendar_id>/sharing")
@login_required
def share_calendar_collection(calendar_id: str):
    actor = str(g.user["username"]); valid_users = {row["username"] for row in get_db().execute("SELECT username FROM user").fetchall()}
    try:
        _calendars().update_sharing(calendar_id, {user: request.form.get(f"access_{user}", "") for user in valid_users}, actor)
        flash("Kalenderfreigaben gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar") + "#caldav")


@bp.get("/calendar/export.ics")
@login_required
def export_calendar():
    payload = _calendar().export_ics(str(g.user["username"])).encode("utf-8")
    return send_file(io.BytesIO(payload), as_attachment=True, download_name="simpleoffice-kalender.ics", mimetype="text/calendar; charset=utf-8")


@bp.post("/calendar/import")
@login_required
def import_calendar():
    uploaded = request.files.get("calendar_file")
    if uploaded is None or not uploaded.filename:
        flash("Bitte eine .ics-Datei auswählen.")
        return redirect(url_for("documents.calendar"))
    try:
        imported = _calendar().import_ics(uploaded.read().decode("utf-8-sig"), str(g.user["username"]))
        flash(f"{imported} Kalendertermin(e) importiert.")
    except (UnicodeDecodeError, ValueError) as exc:
        flash(f"Kalenderimport fehlgeschlagen: {exc}")
    return redirect(url_for("documents.calendar"))


@bp.post("/calendar/import/preview")
@login_required
def preview_calendar_import():
    uploaded = request.files.get("calendar_file")
    if uploaded is None or not uploaded.filename:
        flash("Bitte eine .ics-Datei auswählen.")
        return redirect(url_for("documents.calendar") + "#calendar-import")
    try:
        payload = uploaded.stream.read(MAX_PREVIEW_BYTES + 1)
        if len(payload) > MAX_PREVIEW_BYTES:
            raise ValueError(f"iCalendar preview is limited to {MAX_PREVIEW_BYTES // 1024} KiB")
        preview = preview_ics(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        flash(f"Kalendervorschau fehlgeschlagen: {exc}")
        return redirect(url_for("documents.calendar") + "#calendar-import")
    return render_template(
        "documents/calendar_import_preview.html",
        preview=preview,
        filename=uploaded.filename,
    )


@bp.post("/calendar")
@login_required
def add_calendar_event():
    actor = str(g.user["username"])
    owner = request.form.get("owner", actor).strip() or actor
    valid_users = {row["username"] for row in get_db().execute("SELECT username FROM user").fetchall()}
    try:
        if owner not in valid_users:
            raise ValueError("unknown owner")
        calendar_id = request.form.get("calendar_id", "default")
        _calendars().get(calendar_id, actor, write=True)
        metadata = {**_calendar_metadata(), "description_html": request.form.get("description_html", ""), "description_format": request.form.get("description_format", "text")}
        event = _calendar().add(request.form.get("title", ""), request.form.get("reason", ""), request.form.get("start", ""), request.form.get("end", ""), request.form.get("contact_id", ""), actor, request.form.get("visibility", "private"), request.form.get("public_notice", ""), _calendar_tags(), owner, calendar_id, metadata)
        if request.form.get("rrule", "").strip() or request.form.get("rdates", "").strip():
            event = _calendar().set_recurrence(event["event_id"], {"rrule": request.form.get("rrule", ""), "rdates": request.form.get("rdates", "").splitlines(), "exdates": request.form.get("exdates", "").splitlines(), "timezone": request.form.get("recurrence_timezone", "Europe/Berlin")}, actor, event.get("updated_at", ""))
        _calendars().record_event_move(event, calendar_id, actor)
        flash("Kalendertermin gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar"))


@bp.post("/calendar/<event_id>")
@login_required
def update_calendar_event(event_id: str):
    try:
        actor = str(g.user["username"]); calendar_id = request.form.get("calendar_id", "")
        if calendar_id: _calendars().get(calendar_id, actor, write=True)
        source_calendar_id = _calendar().get(event_id, actor).get("calendar_id") or "default"
        metadata = {**_calendar_metadata(), "description_html": request.form.get("description_html", ""), "description_format": request.form.get("description_format", "text")}
        event = _calendar().update(event_id, request.form.get("title", ""), request.form.get("reason", ""), request.form.get("start", ""), request.form.get("end", ""), request.form.get("contact_id", ""), actor, request.form.get("visibility", "private"), request.form.get("public_notice", ""), _calendar_tags(), calendar_id, metadata)
        _calendars().record_event_move(event, source_calendar_id, actor)
        flash("Kalendertermin geändert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar"))


@bp.post("/calendar/<event_id>/participants")
@login_required
def update_calendar_participants(event_id: str):
    participants = []
    try:
        for line in request.form.get("participants", "").splitlines():
            if not line.strip(): continue
            email, name, role, status, rsvp = (line.split("|") + ["", "", "", "", ""])[:5]
            participants.append({"email": email.strip(), "name": name.strip(), "role": role.strip() or "required", "status": status.strip() or "needs-action", "rsvp": rsvp.strip().lower() in {"1", "true", "ja", "yes"}})
        _calendar().set_participants(event_id, participants, str(g.user["username"]))
        flash("Teilnehmer gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar") + f"#event-{event_id}")


@bp.post("/calendar/<event_id>/recurrence")
@login_required
def update_calendar_recurrence(event_id: str):
    actor = str(g.user["username"])
    try:
        previous = _calendar().get(event_id, actor); calendar_id = previous.get("calendar_id") or "default"
        event = _calendar().set_recurrence(event_id, {"rrule": request.form.get("rrule", ""), "rdates": request.form.get("rdates", "").splitlines(), "exdates": request.form.get("exdates", "").splitlines(), "timezone": request.form.get("recurrence_timezone", "")}, actor, request.form.get("expected_updated_at", ""))
        _calendars().record_event_move(event, calendar_id, actor)
        flash("Serienregel gespeichert und für CalDAV synchronisiert.")
    except ValueError as exc:
        flash(f"Serienregel nicht gespeichert: {exc}")
    return redirect(url_for("documents.calendar") + f"#event-{event_id}")


@bp.post("/calendar/<event_id>/occurrence")
@login_required
def update_calendar_occurrence(event_id: str):
    actor = str(g.user["username"])
    try:
        previous = _calendar().get(event_id, actor); calendar_id = previous.get("calendar_id") or "default"
        event = _calendar().set_occurrence_exception(event_id, request.form.get("recurrence_id", ""), actor, status=request.form.get("occurrence_status", "active"), start=request.form.get("occurrence_start", ""), end=request.form.get("occurrence_end", ""), title=request.form.get("occurrence_title", ""), reason=request.form.get("occurrence_reason", ""), expected_updated_at=request.form.get("expected_updated_at", ""))
        _calendars().record_event_move(event, calendar_id, actor)
        flash("Einzelne Serieninstanz revisionssicher geändert.")
    except ValueError as exc:
        flash(f"Serieninstanz nicht geändert: {exc}")
    return redirect(url_for("documents.calendar") + f"#event-{event_id}")


@bp.post("/calendar/<event_id>/alarms")
@login_required
def add_calendar_alarm(event_id: str):
    actor = str(g.user["username"])
    try:
        previous = _calendar().get(event_id, actor)
        minutes = int(request.form.get("minutes", "15"))
        if not 0 <= minutes <= 527040:
            raise ValueError("Erinnerungsabstand muss zwischen 0 und 527040 Minuten liegen.")
        direction = request.form.get("direction", "before")
        related = request.form.get("related", "start")
        if direction not in {"before", "after"} or related not in {"start", "end"}:
            raise ValueError("Ungültiger Erinnerungsbezug.")
        alarms = list(previous.get("alarms", []))
        alarms.append({"action": "DISPLAY", "description": request.form.get("description", "").strip() or previous.get("title", "Erinnerung"), "trigger": {"kind": "relative", "seconds": minutes * 60 * (-1 if direction == "before" else 1), "related": related}})
        event = _calendar().set_alarms(event_id, alarms, actor, request.form.get("expected_updated_at", ""))
        _calendars().record_event_move(event, previous.get("calendar_id") or "default", actor)
        flash("Lokale Kalendererinnerung gespeichert und für CalDAV synchronisiert.")
    except (TypeError, ValueError) as exc:
        flash(f"Erinnerung nicht gespeichert: {exc}")
    return redirect(url_for("documents.calendar") + "#reminders")


@bp.post("/calendar/<event_id>/alarms/delete")
@login_required
def delete_calendar_alarm(event_id: str):
    actor = str(g.user["username"])
    try:
        previous = _calendar().get(event_id, actor); alarm_uid = request.form.get("alarm_uid", "")
        alarms = [item for item in previous.get("alarms", []) if item.get("uid") != alarm_uid]
        if len(alarms) == len(previous.get("alarms", [])):
            raise ValueError("Unbekannte Kalendererinnerung.")
        event = _calendar().set_alarms(event_id, alarms, actor, request.form.get("expected_updated_at", ""))
        _calendars().record_event_move(event, previous.get("calendar_id") or "default", actor)
        flash("Kalendererinnerung entfernt.")
    except ValueError as exc:
        flash(f"Erinnerung nicht entfernt: {exc}")
    return redirect(url_for("documents.calendar") + "#reminders")


@bp.post("/calendar/<event_id>/alarms/acknowledge")
@login_required
def acknowledge_calendar_alarm(event_id: str):
    actor = str(g.user["username"])
    try:
        previous = _calendar().get(event_id, actor)
        event = _calendar().acknowledge_alarm(event_id, request.form.get("alarm_uid", ""), actor)
        _calendars().record_event_move(event, previous.get("calendar_id") or "default", actor)
        flash("Erinnerung bestätigt.")
    except ValueError as exc:
        flash(f"Erinnerung nicht bestätigt: {exc}")
    return redirect(url_for("documents.calendar") + "#reminders")


@bp.post("/calendar/<event_id>/alarms/snooze")
@login_required
def snooze_calendar_alarm(event_id: str):
    actor = str(g.user["username"])
    try:
        previous = _calendar().get(event_id, actor)
        event = _calendar().snooze_alarm(event_id, request.form.get("alarm_uid", ""), actor, int(request.form.get("minutes", "10")))
        _calendars().record_event_move(event, previous.get("calendar_id") or "default", actor)
        flash("Erinnerung wurde verschoben.")
    except (TypeError, ValueError) as exc:
        flash(f"Erinnerung nicht verschoben: {exc}")
    return redirect(url_for("documents.calendar") + "#reminders")


@bp.get("/calendar/reminders.json")
@login_required
def calendar_reminders_json():
    now = datetime.now(timezone.utc)
    try:
        lower = datetime.fromisoformat(request.args.get("from", "").replace("Z", "+00:00")) if request.args.get("from") else now - timedelta(hours=12)
        upper = datetime.fromisoformat(request.args.get("to", "").replace("Z", "+00:00")) if request.args.get("to") else now + timedelta(days=7)
        rows = _calendar().due_alarms(str(g.user["username"]), lower, upper, request.args.get("calendar_id", ""))
        return Response(json.dumps({"generated_at": now.isoformat(timespec="seconds"), "reminders": rows}, ensure_ascii=False), mimetype="application/json")
    except ValueError as exc:
        return Response(json.dumps({"error": str(exc)}, ensure_ascii=False), 400, mimetype="application/json")


@bp.post("/calendar/<event_id>/delete")
@login_required
def delete_calendar_event(event_id: str):
    try:
        _calendar().delete(event_id, str(g.user["username"]))
        flash("Kalendertermin gelöscht.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar"))


@bp.post("/calendar/<event_id>/sharing")
@login_required
def share_calendar_event(event_id: str):
    actor = str(g.user["username"])
    valid_users = {row["username"] for row in get_db().execute("SELECT username FROM user").fetchall()}
    permissions = {username: request.form.get(f"access_{username}", "") for username in valid_users}
    unknown = sorted(set(request.form.getlist("users")) - valid_users)
    try:
        if unknown:
            raise ValueError(f"unknown users: {', '.join(unknown)}")
        _calendar().share(event_id, permissions, actor)
        flash("Lesen- und Bearbeitungsrechte für den Termin gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar") + f"#event-{event_id}")


@bp.get("/calendar/published/<audience>")
def published_calendar(audience: str):
    try:
        return render_template("documents/published_calendar.html", audience=audience, events=_calendar().visible_events(audience))
    except ValueError:
        abort(404)


@bp.post("/calendar/booking-settings")
@login_required
def save_booking_settings():
    try:
        _calendar().save_booking_settings(request.form.get("enabled") == "1", int(request.form.get("duration_minutes", "60")), request.form.get("start_time", "09:00"), request.form.get("end_time", "17:00"), str(g.user["username"]), request.form.get("timezone", "Europe/Berlin"))
        flash("Externe Buchungseinstellungen gespeichert.")
    except (TypeError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar"))


@bp.post("/calendar/bookings/<event_id>/confirm")
@login_required
def confirm_booking(event_id: str):
    try:
        event = _calendar().confirm_booking(event_id, str(g.user["username"]))
        if event.get("confirmation_delivery", {}).get("status") == "sent":
            flash("Buchung bestätigt und ICS-E-Mail versendet.")
        else:
            flash("Buchung bestätigt und verbindlich blockiert. E-Mail-Versand ist ausstehend; die ICS-Datei kann im Termin heruntergeladen werden.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar"))


@bp.get("/calendar/bookings/<event_id>/confirmation.ics")
@login_required
def download_booking_confirmation(event_id: str):
    try:
        payload = _calendar().booking_ics(event_id, str(g.user["username"])).encode("utf-8")
    except ValueError:
        abort(404)
    return send_file(io.BytesIO(payload), as_attachment=True, download_name=f"terminbestaetigung-{event_id}.ics", mimetype="text/calendar; charset=utf-8")


@bp.route("/calendar/book", methods=("GET", "POST"))
def book_calendar_slot():
    from datetime import date
    selected_day = request.values.get("date", date.today().isoformat())
    try:
        slots = _calendar().available_slots(date.fromisoformat(selected_day))
        if request.method == "POST":
            _calendar().request_booking(request.form.get("title", ""), request.form.get("reason", ""), request.form.get("name", ""), request.form.get("email", ""), request.form.get("start", ""), request.form.get("end", ""))
            return render_template("documents/book_calendar.html", date=selected_day, slots=slots, sent=True)
        return render_template("documents/book_calendar.html", date=selected_day, slots=slots)
    except ValueError as exc:
        return render_template("documents/book_calendar.html", date=selected_day, slots=[], error=str(exc)), 400


@bp.get("/contacts/<contact_id>.vcf")
@login_required
def download_contact_vcard(contact_id: str):
    try:
        card = _contacts().vcard(contact_id, str(g.user["username"]))
    except ValueError:
        abort(404)
    return Response(card, mimetype="text/vcard", headers={"Content-Disposition": f'attachment; filename="contact-{contact_id}.vcf"'})
